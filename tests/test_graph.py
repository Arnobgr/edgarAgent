"""End-to-end graph run with a stub retriever and a fake LLM — no network."""

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import Field

from fie.context import SYNTHESIS_SYSTEM_PROMPT
from fie.graph import build_graph
from fie.state import EngineState, EvidenceChunk


class RecordingFakeChatModel(GenericFakeChatModel):
    """Fake chat model that records the prompts it was invoked with."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class StubRetriever:
    def __init__(self, chunks: list[EvidenceChunk]) -> None:
        self.chunks = chunks
        self.queries: list[str] = []

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        self.queries.append(query)
        return self.chunks[:k]


def make_chunks() -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id=f"c{i}",
            doc_id="AAPL-10-K-test",
            ticker="AAPL",
            form_type="10-K",
            section="Item 7",
            text=f"Evidence text {i} about revenue segments.",
            score=1.0 / i,
            metadata={"accession_no": "test-acc", "filing_date": "2025-10-31"},
        )
        for i in range(1, 4)
    ]


def test_graph_runs_end_to_end_with_mocked_llm() -> None:
    retriever = StubRetriever(make_chunks())
    answer = "iPhone was the largest segment [c1], followed by Services [c2]."
    fake_model = GenericFakeChatModel(messages=iter([AIMessage(content=answer)]))

    graph = build_graph(retriever, fake_model, k=2)
    result = graph.invoke(EngineState(question="What were the main revenue segments?"))

    # Research node ran the retrieval with the question and packed top-k.
    assert retriever.queries == ["What were the main revenue segments?"]
    assert [c.chunk_id for c in result["evidence"]] == ["c1", "c2"]
    # Synthesize node produced the cited draft answer.
    assert result["draft_answer"] == answer
    assert "[c1]" in result["draft_answer"]
    # Unused blueprint fields survive the run with stable empty defaults.
    assert result["claims"] == []
    assert result["rejection_feedback"] == []


def test_evidence_and_instructions_reach_the_model_prompt() -> None:
    retriever = StubRetriever(make_chunks())
    fake_model = RecordingFakeChatModel(messages=iter([AIMessage(content="ok")]), calls=[])
    graph = build_graph(retriever, fake_model, k=3)

    graph.invoke(EngineState(question="What were the main revenue segments?"))

    assert len(fake_model.calls) == 1
    prompt = fake_model.calls[0]
    assert prompt[0].content == SYNTHESIS_SYSTEM_PROMPT
    human_text = str(prompt[1].content)
    assert "What were the main revenue segments?" in human_text
    # Each packed chunk appears with its citation id and source header.
    for chunk_id in ("c1", "c2", "c3"):
        assert f"[{chunk_id}] AAPL 10-K (2025-10-31) — Item 7" in human_text
