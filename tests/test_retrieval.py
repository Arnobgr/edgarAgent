"""Qdrant hybrid retriever: query construction, filter inference, rerank
application — over a fake Qdrant client (no server, no models)."""

from types import SimpleNamespace
from typing import Any

import pytest
from qdrant_client import models

from fie.state import EvidenceChunk
from fie.tools.retrieval import (
    NoopReranker,
    QdrantHybridRetriever,
    Retriever,
    SparseVector,
    chunk_point_id,
)
from fie.tools.retrieval.qdrant import _payload, _to_chunk, infer_filter

TICKERS = ["TSLA", "AAPL", "NVDA"]


def make_chunk(chunk_id: str, ticker: str = "AAPL", section: str = "Item 7") -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        doc_id=f"{ticker}-10-K-acc-1",
        ticker=ticker,
        form_type="10-K",
        section=section,
        text=f"text of {chunk_id}",
        metadata={
            "accession_no": "acc-1",
            "filing_date": "2025-10-31",
            "fiscal_period": "FY2025",
            "fiscal_year": "2025",
            "chunk_type": "text",
            "anchor": chunk_id,
        },
    )


class FakeEmbedder:
    model = "fake-embed"
    dim = 3

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        self.calls.append((texts, input_type))
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeSparseEncoder:
    def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        return [SparseVector(indices=[1], values=[1.0]) for _ in texts]

    def encode_query(self, text: str) -> SparseVector:
        return SparseVector(indices=[1, 2], values=[0.5, 0.5])


class ReversingReranker:
    """Reverses the fused order — makes rerank application observable."""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        order = list(reversed(range(len(documents))))[:top_k]
        return [(i, float(len(documents) - rank)) for rank, i in enumerate(order)]


class FakeQdrantClient:
    def __init__(self, chunks: list[EvidenceChunk]) -> None:
        self.chunks = chunks
        self.query_calls: list[dict[str, Any]] = []

    def query_points(self, **kwargs: Any) -> Any:
        self.query_calls.append(kwargs)
        points = [
            models.ScoredPoint(
                id=chunk_point_id(c.chunk_id),
                version=0,
                score=1.0 / (i + 1),
                payload=_payload(c),
            )
            for i, c in enumerate(self.chunks[: kwargs["limit"]])
        ]
        return SimpleNamespace(points=points)


def make_retriever(chunks: list[EvidenceChunk], reranker: Any = None) -> QdrantHybridRetriever:
    return QdrantHybridRetriever(
        FakeQdrantClient(chunks),  # type: ignore[arg-type]
        "test-collection",
        embedder=FakeEmbedder(),
        sparse_encoder=FakeSparseEncoder(),
        reranker=reranker or NoopReranker(),
        corpus_tickers=TICKERS,
        prefetch_limit=10,
        candidates=5,
    )


def test_satisfies_retriever_protocol() -> None:
    assert isinstance(make_retriever([make_chunk("c1")]), Retriever)


def test_hybrid_query_prefetches_dense_and_sparse_and_fuses_rrf() -> None:
    retriever = make_retriever([make_chunk("c1")])
    retriever.retrieve("apple services revenue", k=2)

    client: FakeQdrantClient = retriever._client  # type: ignore[assignment]
    call = client.query_calls[0]
    prefetch = call["prefetch"]
    assert [p.using for p in prefetch] == ["dense", "sparse"]
    assert all(p.limit == 10 for p in prefetch)
    assert isinstance(call["query"], models.FusionQuery)
    assert call["query"].fusion == models.Fusion.RRF
    assert call["limit"] == 5


def test_query_uses_query_input_type_embedding() -> None:
    retriever = make_retriever([make_chunk("c1")])
    retriever.retrieve("anything", k=1)
    embedder: FakeEmbedder = retriever._embedder  # type: ignore[assignment]
    assert embedder.calls == [(["anything"], "query")]


def test_reranker_reorders_and_k_is_respected() -> None:
    chunks = [make_chunk(f"c{i}") for i in range(4)]
    retriever = make_retriever(chunks, reranker=ReversingReranker())
    results = retriever.retrieve("q", k=2)

    assert [r.chunk_id for r in results] == ["c3", "c2"]  # reversed fused order
    assert len(results) == 2
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)  # type: ignore[type-var]


def test_payload_roundtrip_preserves_citation_trail() -> None:
    chunk = make_chunk("acc-1:item-7-p001")
    restored = _to_chunk(_payload(chunk), score=0.9)
    assert restored.chunk_id == chunk.chunk_id
    assert restored.section == chunk.section
    assert restored.metadata["accession_no"] == "acc-1"
    assert restored.metadata["fiscal_period"] == "FY2025"
    assert restored.metadata["chunk_type"] == "text"
    assert restored.score == 0.9


def test_point_ids_are_stable() -> None:
    assert chunk_point_id("a:b") == chunk_point_id("a:b")
    assert chunk_point_id("a:b") != chunk_point_id("a:c")


# --- Filter inference (research branches filter by company/period) ---


def _must_conditions(flt: models.Filter | None) -> dict[str, Any]:
    assert flt is not None and flt.must is not None
    out = {}
    for cond in flt.must:
        assert isinstance(cond, models.FieldCondition)
        assert isinstance(cond.match, models.MatchValue)
        out[cond.key] = cond.match.value
    return out


def test_single_company_mention_filters_ticker() -> None:
    flt = infer_filter("How did Tesla's margins evolve?", TICKERS)
    assert _must_conditions(flt) == {"ticker": "TSLA"}


def test_company_alias_and_symbol_both_match() -> None:
    assert _must_conditions(infer_filter("nvidia data center", TICKERS)) == {"ticker": "NVDA"}
    assert _must_conditions(infer_filter("NVDA data center", TICKERS)) == {"ticker": "NVDA"}


def test_multiple_companies_apply_no_ticker_filter() -> None:
    assert infer_filter("Compare Apple and Nvidia revenue", TICKERS) is None


def test_fiscal_year_mention_filters_year() -> None:
    flt = infer_filter("Tesla revenue in fiscal 2024", TICKERS)
    assert _must_conditions(flt) == {"ticker": "TSLA", "fiscal_year": 2024}


def test_quarter_mention_filters_period() -> None:
    flt = infer_filter("Apple Q2 2025 results", TICKERS)
    assert _must_conditions(flt) == {"ticker": "AAPL", "fiscal_period": "Q2 2025"}


def test_two_years_apply_no_period_filter() -> None:
    flt = infer_filter("apple revenue fiscal 2024 versus fiscal 2023", TICKERS)
    assert _must_conditions(flt) == {"ticker": "AAPL"}


def test_no_mentions_no_filter() -> None:
    assert infer_filter("total revenue growth", TICKERS) is None


def test_empty_fused_result_returns_empty() -> None:
    retriever = make_retriever([])
    assert retriever.retrieve("q", k=3) == []


@pytest.mark.parametrize("query", ["tesla 2024 and 2025 revenue", "revenues in 2024"])
def test_bare_years_do_not_create_period_filter(query: str) -> None:
    flt = infer_filter(query, TICKERS)
    conditions = _must_conditions(flt) if flt is not None else {}
    assert "fiscal_year" not in conditions
    assert "fiscal_period" not in conditions
