"""Shared test fakes: structured-output chat model, retriever, reconciler spy,
a graph factory with fast limits, and the HTTP harness — no network anywhere."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import FastAPI
from langchain_core.callbacks import BaseCallbackHandler, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from pydantic import Field

from fie.graph import GraphLimits, build_graph
from fie.guardrails import BudgetTracker
from fie.state import (
    DecomposeOutput,
    DraftClaim,
    EngineState,
    EvidenceChunk,
    GroundingRow,
    JudgeOutput,
    ReviewDecision,
    ReviewMode,
    RunResult,
)
from fie.tools.reconcile import InterimNumericReconciler, ReconcileOutcome


class StructuredFakeModel(BaseChatModel):
    """Fake chat model whose structured output pops from a queue.

    Records every prompt in `calls`; falls back to `default_output` when the
    queue is empty; can fail transiently (`fail_times`) or run slow
    (`delay_seconds`) to exercise retry and timeout guardrails.
    """

    outputs: list[Any] = Field(default_factory=list)
    default_output: Any = None
    calls: list[Any] = Field(default_factory=list)
    fail_times: int = 0
    # 1-indexed call numbers that raise, for failing a *specific* call (e.g. a
    # node's repair re-ask on call 2 while its first call succeeds), which
    # `fail_times` — which fails the earliest calls — cannot express.
    fail_calls: set[int] = Field(default_factory=set)
    delay_seconds: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "structured-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("StructuredFakeModel only supports structured output")

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        async def run(value: Any) -> Any:
            self.calls.append(value)
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if len(self.calls) in self.fail_calls:
                raise RuntimeError(f"fake failure on call {len(self.calls)}")
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("transient fake failure")
            if self.outputs:
                return self.outputs.pop(0)
            if self.default_output is not None:
                return self.default_output
            raise AssertionError(f"StructuredFakeModel has no queued output for {schema}")

        return RunnableLambda(run)


class StubRetriever:
    """Returns fixed chunks; records every query."""

    def __init__(self, chunks: list[EvidenceChunk]) -> None:
        self.chunks = chunks
        self.queries: list[str] = []

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        self.queries.append(query)
        return self.chunks[:k]


class SpyReconciler:
    """Returns a fixed outcome; records every reconciliation call."""

    def __init__(self, outcome: ReconcileOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, list[str]]] = []

    def reconcile(self, claim_text: str, chunks: list[EvidenceChunk]) -> ReconcileOutcome:
        self.calls.append((claim_text, [c.chunk_id for c in chunks]))
        return self.outcome


def make_chunks(texts: dict[str, str] | None = None) -> list[EvidenceChunk]:
    """Evidence chunks keyed by chunk id (defaults to three generic chunks)."""
    texts = texts or {f"c{i}": f"Evidence text {i} about revenue segments." for i in (1, 2, 3)}
    return [
        EvidenceChunk(
            chunk_id=chunk_id,
            doc_id="AAPL-10-K-test",
            ticker="AAPL",
            form_type="10-K",
            section="Item 7",
            text=text,
            score=1.0,
            metadata={"accession_no": "test-acc", "filing_date": "2025-10-31"},
        )
        for chunk_id, text in texts.items()
    ]


def any_claim(chunk_id: str = "c1") -> DraftClaim:
    """One claim citing one chunk, for a synthesis fake whose claims are not
    what the test is about.

    A draft carrying no claims fails the run since ADR 0038, so a test about
    decomposition or the critic cycle still has to make *a* claim. Cite a
    chunk the test's retriever actually returns, and pair it with
    `grounded_judge()`: an unsupported claim loops back to research and moves
    the call counts those tests assert on."""
    return DraftClaim(
        text="a claim this test does not care about",
        claim_type="qualitative",
        source_chunk_ids=[chunk_id],
    )


def grounded_judge() -> StructuredFakeModel:
    """An entailment judge that grounds whatever it is shown."""
    return StructuredFakeModel(
        default_output=JudgeOutput(verdict="grounded", supporting_span="span")
    )


def fast_limits(**overrides: Any) -> GraphLimits:
    defaults: dict[str, Any] = {
        "node_timeout_seconds": 5.0,
        "retry_attempts": 1,
        "retry_base_delay_seconds": 0.01,
    }
    defaults.update(overrides)
    return GraphLimits(**defaults)


def big_tracker() -> BudgetTracker:
    return BudgetTracker(max_tokens=10**9, max_cost_usd=10**6)


def make_graph(
    *,
    retriever: Any,
    synthesis: StructuredFakeModel,
    decompose: StructuredFakeModel | None = None,
    qualitative: StructuredFakeModel | None = None,
    forward: StructuredFakeModel | None = None,
    critic: StructuredFakeModel | None = None,
    rewrite: StructuredFakeModel | None = None,
    reconciler: Any = None,
    fact_lookup: Any = None,
    tracker: BudgetTracker | None = None,
    limits: GraphLimits | None = None,
    k: int = 6,
    review_mode: ReviewMode = "off",
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[EngineState]:
    # Default decomposer: empty split → the single-part passthrough (one
    # sub-question carrying the question verbatim), so Phase 1-era tests see
    # the same research query they always did.
    decompose = decompose or StructuredFakeModel(default_output=DecomposeOutput())
    return build_graph(
        retriever=retriever,
        decompose_model=decompose,
        synthesis_model=synthesis,
        qualitative_model=qualitative or StructuredFakeModel(),
        forward_model=forward or StructuredFakeModel(),
        critic_model=critic or StructuredFakeModel(),
        rewrite_model=rewrite,
        reconciler=reconciler or InterimNumericReconciler(),
        fact_lookup=fact_lookup,
        tracker=tracker or big_tracker(),
        limits=limits or fast_limits(),
        k=k,
        review_mode=review_mode,
        checkpointer=checkpointer,
    )


# --- The HTTP surface ---


def make_result(
    run_id: str = "job-1",
    *,
    question: str = "What were Apple's total net sales?",
    answer: str = "Total net sales were $391.0 billion [c1].",
    grounding: list[GroundingRow] | None = None,
    awaiting_review: bool = False,
    error: str | None = None,
) -> RunResult:
    """A minimal `RunResult` for the API tests, which care about how a result
    travels rather than how it was derived."""
    rows = grounding or [
        GroundingRow(
            claim_id="c-1",
            text="Total net sales were $391.0 billion",
            claim_type="numeric",
            status="grounded",
            verdict="grounded",
        )
    ]
    return RunResult(
        run_id=run_id,
        question=question,
        answer=answer,
        grounding=rows,
        awaiting_review=awaiting_review,
        error=error,
    )


class FakeEngine:
    """Stands in for `answer_question` behind the API.

    Records every call, which is the only way a test can see what the HTTP
    surface actually asked the engine to do: that a resume passes
    `question=None`, and that a review submission carries the reviewer's
    decisions rather than dropping them.

    `hold` lets a test park runs in flight — the semaphore and queue-cap
    assertions need concurrent jobs that do not finish on their own — and
    `peak_concurrent` records how many the registry ever admitted at once.
    """

    def __init__(
        self,
        *,
        results: list[RunResult] | None = None,
        default: RunResult | None = None,
        hold: asyncio.Event | None = None,
        raises: Exception | None = None,
        usage: tuple[int, int, str] | None = None,
    ) -> None:
        self.results = list(results or [])
        self.default = default or make_result()
        self.hold = hold
        self.raises = raises
        self.usage = usage
        self.calls: list[dict[str, Any]] = []
        self.concurrent = 0
        self.peak_concurrent = 0

    async def __call__(
        self,
        *,
        question: str | None,
        run_id: str,
        review_decisions: list[ReviewDecision] | None,
        extra_callbacks: list[BaseCallbackHandler],
    ) -> RunResult:
        self.calls.append(
            {
                "question": question,
                "run_id": run_id,
                "review_decisions": review_decisions,
                "log_context": structlog.contextvars.get_contextvars(),
            }
        )
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            if self.hold is not None:
                await self.hold.wait()
            for callback in extra_callbacks:
                if isinstance(callback, BudgetTracker) and self.usage:
                    callback.record(*self.usage)
            if self.raises is not None:
                raise self.raises
            result = self.results.pop(0) if self.results else self.default
        finally:
            self.concurrent -= 1
        return result.model_copy(update={"run_id": run_id})


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Drive the app over ASGI on *this* event loop.

    `TestClient` would run it in a second thread with its own loop, which puts
    the background job tasks out of reach of a test that needs to hold one
    open or release it at a chosen moment. Running the transport directly
    keeps the app, its jobs and the test on one loop; the lifespan is entered
    explicitly because `ASGITransport` does not.
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://fie.test") as client:
            yield client


async def wait_for_status(
    client: httpx.AsyncClient,
    job_id: str,
    *,
    headers: dict[str, str],
    until: set[str],
    tries: int = 500,
) -> dict[str, Any]:
    """Poll a job the way the frontend does, until it reaches one of `until`."""
    for _ in range(tries):
        response = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        body: dict[str, Any] = response.json()
        if body["status"] in until:
            return body
        await asyncio.sleep(0)
    raise AssertionError(f"job {job_id} never reached {until}")
