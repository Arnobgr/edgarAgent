import pytest

from fie.state import EvidenceChunk
from fie.tools.retrieval import BM25Retriever, Retriever


def make_chunk(chunk_id: str, section: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        doc_id="AAPL-10-K-test",
        ticker="AAPL",
        form_type="10-K",
        section=section,
        text=text,
        metadata={"accession_no": "test-acc", "filing_date": "2025-10-31"},
    )


@pytest.fixture
def chunks() -> list[EvidenceChunk]:
    return [
        make_chunk("c1", "Item 1", "Apple designs iPhone, Mac, iPad and wearables."),
        make_chunk("c2", "Item 1A", "Risks include supply chain concentration and competition."),
        make_chunk(
            "c3",
            "Item 7",
            "Net sales by segment: iPhone revenue, Services revenue and Mac revenue "
            "for the fiscal year.",
        ),
        make_chunk("c4", "Item 8", "Consolidated balance sheets and statements of operations."),
    ]


def test_bm25_satisfies_retriever_protocol(chunks: list[EvidenceChunk]) -> None:
    retriever = BM25Retriever(chunks)
    assert isinstance(retriever, Retriever)


def test_relevant_chunk_ranks_first(chunks: list[EvidenceChunk]) -> None:
    retriever = BM25Retriever(chunks)
    results = retriever.retrieve("revenue by segment for the fiscal year", k=2)

    assert len(results) == 2
    assert results[0].chunk_id == "c3"


def test_k_is_respected_and_scores_are_set(chunks: list[EvidenceChunk]) -> None:
    retriever = BM25Retriever(chunks)
    results = retriever.retrieve("Apple", k=3)

    assert len(results) == 3
    assert all(r.score is not None for r in results)
    scores = [r.score for r in results if r.score is not None]
    assert scores == sorted(scores, reverse=True)


def test_source_chunks_are_not_mutated(chunks: list[EvidenceChunk]) -> None:
    retriever = BM25Retriever(chunks)
    retriever.retrieve("revenue", k=4)
    assert all(c.score is None for c in chunks)


def test_empty_corpus_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one chunk"):
        BM25Retriever([])
