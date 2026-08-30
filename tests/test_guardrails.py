"""Guardrails: budget accounting, retry-with-backoff, node wrapper."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableConfig, RunnableLambda

from fie.guardrails import (
    BudgetExceededError,
    BudgetTracker,
    EmptyStructuredOutputError,
    ainvoke_with_retry,
    guarded,
)
from fie.state import EngineState

PRICES = {"claude-opus-4-8": (5.0, 25.0)}


def _llm_result(input_tokens: int, output_tokens: int, model: str) -> LLMResult:
    message = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_tracker_accumulates_tokens_and_cost_from_callback() -> None:
    tracker = BudgetTracker(max_tokens=1000, max_cost_usd=1.0, prices=PRICES)
    tracker.on_llm_end(_llm_result(100, 50, "claude-opus-4-8-20250115"), run_id=uuid4())
    assert tracker.tokens_used == 150
    assert tracker.cost_usd_used == pytest.approx(100 * 5 / 1e6 + 50 * 25 / 1e6)
    assert not tracker.exceeded


def test_tracker_unknown_model_counts_tokens_but_no_cost() -> None:
    tracker = BudgetTracker(max_tokens=1000, max_cost_usd=1.0, prices=PRICES)
    tracker.record(500, 100, "some/unknown-model")
    assert tracker.tokens_used == 600
    assert tracker.cost_usd_used == 0.0


def test_tracker_skips_a_generation_with_no_usage() -> None:
    """A provider response can carry a generation with no usage metadata; it is
    skipped, not counted, so a partial response cannot crash the callback."""
    tracker = BudgetTracker(max_tokens=1000, max_cost_usd=1.0, prices=PRICES)
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="no usage"))]])
    tracker.on_llm_end(result, run_id=uuid4())
    assert tracker.tokens_used == 0
    assert tracker.cost_usd_used == 0.0


def test_tracker_exceeded_on_either_cap() -> None:
    tracker = BudgetTracker(max_tokens=100, max_cost_usd=1.0, prices=PRICES)
    tracker.record(101, 0, "")
    assert tracker.exceeded

    tracker = BudgetTracker(max_tokens=10**9, max_cost_usd=0.001, prices=PRICES)
    tracker.record(0, 1000, "claude-opus-4-8")  # $0.025
    assert tracker.exceeded


def test_tracker_snapshot_matches_state_model() -> None:
    tracker = BudgetTracker(max_tokens=100, max_cost_usd=2.0, prices=PRICES)
    tracker.record(10, 5, "claude-opus-4-8")
    snapshot = tracker.snapshot()
    assert snapshot.max_tokens == 100
    assert snapshot.tokens_used == 15
    assert snapshot.cost_usd_used == pytest.approx(10 * 5 / 1e6 + 5 * 25 / 1e6)


async def test_retry_recovers_from_transient_failures() -> None:
    attempts = 0

    async def flaky(_: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await ainvoke_with_retry(
        RunnableLambda(flaky), None, attempts=3, base_delay_seconds=0.001
    )
    assert result == "ok"
    assert attempts == 3


async def test_retry_raises_after_exhaustion() -> None:
    async def broken(_: Any) -> str:
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        await ainvoke_with_retry(RunnableLambda(broken), None, attempts=2, base_delay_seconds=0.001)


async def test_retry_recovers_from_empty_structured_output() -> None:
    """A structured-output runnable yields None — not an exception — when the
    model answers in prose instead of calling the tool. Retry it."""
    attempts = 0

    async def prose_then_tool_call(_: Any) -> str | None:
        nonlocal attempts
        attempts += 1
        return None if attempts < 2 else "ok"

    result = await ainvoke_with_retry(
        RunnableLambda(prose_then_tool_call), None, attempts=3, base_delay_seconds=0.001
    )
    assert result == "ok"
    assert attempts == 2


async def test_persistent_empty_structured_output_raises() -> None:
    async def never_calls_tool(_: Any) -> None:
        return None

    with pytest.raises(EmptyStructuredOutputError):
        await ainvoke_with_retry(
            RunnableLambda(never_calls_tool), None, attempts=2, base_delay_seconds=0.001
        )


async def test_retry_refuses_to_call_over_budget() -> None:
    tracker = BudgetTracker(max_tokens=10, max_cost_usd=1.0)
    tracker.record(11, 0)
    calls = 0

    async def never(_: Any) -> str:
        nonlocal calls
        calls += 1
        return "should not run"

    with pytest.raises(BudgetExceededError):
        await ainvoke_with_retry(RunnableLambda(never), None, tracker=tracker)
    assert calls == 0


def _config() -> RunnableConfig:
    return {}


async def test_guarded_short_circuits_on_existing_error() -> None:
    async def node(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        raise AssertionError("must not run")

    wrapped = guarded("n", node, tracker=BudgetTracker(100, 1.0), timeout_seconds=1.0)
    update = await wrapped(EngineState(question="q", error="already failed"), _config())
    assert update == {}


async def test_guarded_aborts_before_node_when_over_budget() -> None:
    tracker = BudgetTracker(max_tokens=10, max_cost_usd=1.0)
    tracker.record(11, 0)

    async def node(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        raise AssertionError("must not run")

    wrapped = guarded("verify", node, tracker=tracker, timeout_seconds=1.0)
    update = await wrapped(EngineState(question="q"), _config())
    assert "budget exceeded" in (update["error"] or "")
    assert update["run_budget"].tokens_used == 11


async def test_guarded_catches_budget_raised_inside_a_node() -> None:
    """A budget abort that fires *during* a node (not the pre-node check)
    still lands as an error on the state, not an exception — the graph routes
    it to END the same way a pre-node abort does."""

    async def node(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        raise BudgetExceededError("spent it mid-node")

    wrapped = guarded("verify", node, tracker=BudgetTracker(100, 1.0), timeout_seconds=1.0)
    update = await wrapped(EngineState(question="q"), _config())
    assert "budget exceeded" in (update["error"] or "")
    assert "spent it mid-node" in (update["error"] or "")


async def test_guarded_times_out_slow_node() -> None:
    async def slow(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        await asyncio.sleep(0.5)
        return {}

    wrapped = guarded("synthesize", slow, tracker=BudgetTracker(100, 1.0), timeout_seconds=0.05)
    update = await wrapped(EngineState(question="q"), _config())
    assert "timed out" in (update["error"] or "")


async def test_guarded_stamps_budget_into_update() -> None:
    tracker = BudgetTracker(max_tokens=100, max_cost_usd=1.0)
    tracker.record(7, 3)

    async def node(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        return {"draft_answer": "hi"}

    wrapped = guarded("n", node, tracker=tracker, timeout_seconds=1.0)
    update = await wrapped(EngineState(question="q"), _config())
    assert update["draft_answer"] == "hi"
    assert update["run_budget"].tokens_used == 10
