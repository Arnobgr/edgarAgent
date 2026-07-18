"""Execution guardrails: retry-with-backoff for LLM/tool calls (one place,
not per-node) and the timeout/budget wrapper applied to every graph node."""

import asyncio
import logging
import random
from collections.abc import Awaitable
from typing import Any, Protocol

from langchain_core.runnables import Runnable, RunnableConfig

from fie.guardrails.budget import BudgetExceededError, BudgetTracker
from fie.state import EngineState

logger = logging.getLogger(__name__)


class NodeFn(Protocol):
    """An async graph node: state + config in, partial state update out."""

    def __call__(self, state: EngineState, config: RunnableConfig) -> Awaitable[dict[str, Any]]: ...


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
    cap stops issuing LLM calls immediately."""
    for attempt in range(1, attempts + 1):
        if tracker is not None and tracker.exceeded:
            raise BudgetExceededError(tracker.describe())
        try:
            return await runnable.ainvoke(value, config=config)
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
    raise AssertionError("unreachable")


def guarded(name: str, fn: NodeFn, *, tracker: BudgetTracker, timeout_seconds: float) -> NodeFn:
    """Wrap a node with the guardrails every node gets: short-circuit when the
    run is already in an error state, refuse to start over budget, bound wall
    time, and stamp the current budget usage into the state."""

    async def wrapped(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        if state.error is not None:
            return {}
        if tracker.exceeded:
            message = f"run aborted before node {name!r}: budget exceeded — {tracker.describe()}"
            return {"error": message, "run_budget": tracker.snapshot()}
        try:
            async with asyncio.timeout(timeout_seconds):
                update = await fn(state, config)
        except TimeoutError:
            return {
                "error": f"node {name!r} timed out after {timeout_seconds:g}s",
                "run_budget": tracker.snapshot(),
            }
        except BudgetExceededError as exc:
            return {
                "error": f"run aborted in node {name!r}: budget exceeded — {exc}",
                "run_budget": tracker.snapshot(),
            }
        update.setdefault("run_budget", tracker.snapshot())
        return update

    wrapped.__name__ = name
    return wrapped
