"""The job registry: in-process asyncio tasks, bounded, persisted in SQLite.

Decision 3.1 (`plans/00-overview.md` §3.1) settled the job model — in-process
asyncio plus polling, no broker and no worker fleet — and this is that
decision as code. The upgrade trigger is documented there; nothing here
anticipates it.

Two bounds, and they guard different things. The **semaphore** is the OOM
control: at most `max_concurrent` graphs hold evidence, claims and an LLM
client at once, sized against the container memory limits rather than to
taste. The **queue cap** is the honesty control: past it, submissions are
refused with `429` instead of being accepted into a queue nobody will reach,
because a job that will not start for twenty minutes is a failure wearing a
`202`.
"""

import asyncio
import logging
from collections.abc import Awaitable
from typing import Protocol

import structlog
from langchain_core.callbacks import BaseCallbackHandler

from fie.api.governor import AnswerCache, SpendGovernor
from fie.api.store import ApiStore, JobStatus
from fie.guardrails import BudgetTracker
from fie.observability import trace_id_for
from fie.state import ReviewDecision, RunResult

logger = logging.getLogger(__name__)


class RunFn(Protocol):
    """One engine run. Production passes `fie.runner.answer_question` bound
    to the public settings; tests pass a fake."""

    def __call__(
        self,
        *,
        question: str | None,
        run_id: str,
        review_decisions: list[ReviewDecision] | None,
        extra_callbacks: list[BaseCallbackHandler],
    ) -> Awaitable[RunResult]: ...


class JobRegistry:
    """Launches and bounds the graph runs behind the HTTP surface."""

    def __init__(
        self,
        *,
        store: ApiStore,
        run_fn: RunFn,
        governor: SpendGovernor,
        cache: AnswerCache,
        model_prices: dict[str, tuple[float, float]],
        max_concurrent: int,
        max_queued: int,
    ) -> None:
        self._store = store
        self._run_fn = run_fn
        self._governor = governor
        self._cache = cache
        self._model_prices = model_prices
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._capacity = max_concurrent + max_queued
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    def has_capacity(self) -> bool:
        return len(self._tasks) < self._capacity

    def launch(
        self,
        job_id: str,
        *,
        question: str | None,
        review_decisions: list[ReviewDecision] | None = None,
    ) -> None:
        """Start (or resume) one job. The caller has already checked
        `has_capacity` and created the row."""
        task = asyncio.create_task(
            self._execute(job_id, question=question, review_decisions=review_decisions),
            name=f"fie-job-{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

    async def shutdown(self) -> None:
        """Cancel in-flight runs on the way down.

        Their rows stay `running`, and the next boot's `recover_orphans`
        marks them `interrupted` and offers them for resume from their
        LangGraph checkpoint. That is the designed path, not a leak."""
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _execute(
        self,
        job_id: str,
        *,
        question: str | None,
        review_decisions: list[ReviewDecision] | None,
    ) -> None:
        # The task runs in a copy of the launching request's log context, so
        # without this every line of the run would carry the POST's
        # `request_id` and `path` and none would carry the job. Clearing it
        # touches only this task's copy. The request's own line already names
        # the job id it returned, so the submission stays correlated.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(job_id=job_id, trace_id=trace_id_for(job_id))
        # A meter, not a cap: the caps live inside `answer_question`, and the
        # tracker it builds is only reachable through a returned RunResult.
        # An exception has no RunResult and the tokens were still bought, so
        # the governor would silently undercount the one path where spending
        # went wrong. Riding along as a callback closes that.
        meter = BudgetTracker(
            max_tokens=2**62, max_cost_usd=float("inf"), prices=self._model_prices
        )
        try:
            async with self._semaphore:
                self._store.set_status(job_id, "running")
                result = await self._run_fn(
                    question=question,
                    run_id=job_id,
                    review_decisions=review_decisions,
                    extra_callbacks=[meter],
                )
        except asyncio.CancelledError:
            self._governor.record(meter.cost_usd_used)
            raise
        except Exception as exc:
            self._governor.record(meter.cost_usd_used)
            logger.exception("job %s failed", job_id)
            self._store.set_status(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
            return

        self._governor.record(meter.cost_usd_used)
        status: JobStatus
        if result.awaiting_review:
            status = "awaiting_review"
        elif result.error:
            status = "failed"
        else:
            status = "succeeded"
        self._store.save_result(
            job_id,
            status=status,
            result_json=result.model_dump_json(),
            cost_usd=result.budget.cost_usd_used,
            tokens=result.budget.tokens_used,
            error=result.error,
        )
        if status == "succeeded":
            row = self._store.get_job(job_id)
            if row is not None:
                self._cache.put(row.question, row.companies, result.model_dump_json())
        logger.info(
            "job %s finished: %s ($%.4f, %d tokens)",
            job_id,
            status,
            meter.cost_usd_used,
            meter.tokens_used,
        )
