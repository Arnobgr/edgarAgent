"""Shared test fakes: structured-output chat model, retriever, reconciler spy,
and a graph factory with fast limits — no network anywhere."""

import asyncio
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from pydantic import Field

from fie.graph import GraphLimits, build_graph
from fie.guardrails import BudgetTracker
from fie.state import EngineState, EvidenceChunk
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
    retriever: StubRetriever,
    synthesis: StructuredFakeModel,
    qualitative: StructuredFakeModel | None = None,
    forward: StructuredFakeModel | None = None,
    critic: StructuredFakeModel | None = None,
    reconciler: Any = None,
    tracker: BudgetTracker | None = None,
    limits: GraphLimits | None = None,
    k: int = 6,
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[EngineState]:
    return build_graph(
        retriever=retriever,
        synthesis_model=synthesis,
        qualitative_model=qualitative or StructuredFakeModel(),
        forward_model=forward or StructuredFakeModel(),
        critic_model=critic or StructuredFakeModel(),
        reconciler=reconciler or InterimNumericReconciler(),
        tracker=tracker or big_tracker(),
        limits=limits or fast_limits(),
        k=k,
        checkpointer=checkpointer,
    )
