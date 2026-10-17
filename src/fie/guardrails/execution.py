"""Execution guardrails: retry-with-backoff for LLM/tool calls (one place,
not per-node) and the timeout/budget wrapper applied to every graph node."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable
from typing import Any, Protocol

from langchain_core.runnables import Runnable, RunnableConfig

from fie.guardrails.budget import BudgetExceededError, BudgetTracker

logger = logging.getLogger(__name__)


class EmptyStructuredOutputError(RuntimeError):
    """A structured-output runnable produced no object.

    `with_structured_output(method="function_calling")` parses tool calls with
    `first_tool_only=True`, and `PydanticToolsParser` **returns `None` instead
    of raising** when the model answered in prose without calling the tool.
    Nothing else in the stack treats that as a failure, so the `None` flows
    into the caller's `cast` and surfaces several lines later as an
    `AttributeError` that kills the whole run (observed 2026-07-27: the GLM
    critic dropped a gold question this way). Raising here turns it into the
    transient failure it is."""


class SupportsError(Protocol):
    """Any node state carrying the run-error channel. Both the main
    `EngineState` and the fan-out `ResearchBranch` payload satisfy this, so
    `guarded` can wrap every node uniformly."""

    @property
    def error(self) -> str | None: ...


class NodeFn[StateT: SupportsError](Protocol):
    """An async graph node: state + config in, partial state update out."""

    def __call__(self, state: StateT, config: RunnableConfig) -> Awaitable[dict[str, Any]]: ...


async def ainvoke_with_retry[T](
    runnable: Runnable[Any, T],
    value: Any,
    config: RunnableConfig | None = None,
    *,
    tracker: BudgetTracker | None = None,
    attempts: int = 3,
    base_delay_seconds: float = 1.0,
) -> T:
    """Invoke `runnable` with bounded exponential backoff (with jitter) on
    transient failures. Checks the budget before every attempt so a run over
    cap stops issuing LLM calls immediately.

    A `None` result counts as a failure and is retried — see
    `EmptyStructuredOutputError` for why the structured-output path can
    produce one without raising."""
    for attempt in range(1, attempts + 1):
        if tracker is not None and tracker.exceeded:
            raise BudgetExceededError(tracker.describe())
        try:
            result = await runnable.ainvoke(value, config=config)
            if result is None:
                raise EmptyStructuredOutputError(
                    "structured output missing: the model returned no tool call"
                )
            return result
        except (BudgetExceededError, asyncio.CancelledError):
            raise
        except Exception:
            if attempt == attempts:
                raise
            delay = base_delay_seconds * 2 ** (attempt - 1) * (0.5 + random.random())
            logger.warning(
                "LLM/tool call failed (attempt %d/%d); retrying in %.1fs",
                attempt,
                attempts,
                delay,
                exc_info=True,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover — the loop returns or raises


def guarded[S: SupportsError](
    name: str,
    fn: NodeFn[S],
    *,
    tracker: BudgetTracker,
    timeout_seconds: float,
) -> NodeFn[S]:
    """Wrap a node with the guardrails every node gets: short-circuit when the
    run is already in an error state, refuse to start over budget, bound wall
    time, and stamp the current budget usage into the state.

    It is also where every node transition is logged, once, in one shape —
    the one place every node passes through, so no node can forget to. The
    job's `job_id`/`trace_id` ride along from the log context the job
    registry binds, which is what lets two interleaved runs be told apart."""

    def transition(outcome: str, started: float) -> None:
        elapsed = time.monotonic() - started
        logger.info(
            "node %s: %s in %.1fs",
            name,
            outcome,
            elapsed,
            extra={
                "node": name,
                "outcome": outcome,
                "duration_ms": round(elapsed * 1000),
                "tokens_used": tracker.tokens_used,
                "cost_usd_used": round(tracker.cost_usd_used, 6),
            },
        )

    async def wrapped(state: S, config: RunnableConfig) -> dict[str, Any]:
        if state.error is not None:
            return {}
        started = time.monotonic()
        if tracker.exceeded:
            transition("over_budget", started)
            message = f"run aborted before node {name!r}: budget exceeded — {tracker.describe()}"
            return {"error": message, "run_budget": tracker.snapshot()}
        try:
            async with asyncio.timeout(timeout_seconds):
                update = await fn(state, config)
        except TimeoutError:
            transition("timeout", started)
            return {
                "error": f"node {name!r} timed out after {timeout_seconds:g}s",
                "run_budget": tracker.snapshot(),
            }
        except BudgetExceededError as exc:
            transition("over_budget", started)
            return {
                "error": f"run aborted in node {name!r}: budget exceeded — {exc}",
                "run_budget": tracker.snapshot(),
            }
        except Exception:
            # A node that raises (a verifier judge still failing after its
            # retries, say) used to leave no transition line at all. It is
            # logged and re-raised unchanged: whether it fails the run is the
            # caller's decision, not this wrapper's.
            transition("raised", started)
            raise
        transition("error" if update.get("error") else "ok", started)
        update.setdefault("run_budget", tracker.snapshot())
        return update

    wrapped.__name__ = name
    return wrapped
