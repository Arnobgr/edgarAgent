"""Retrieval: the interface all retrievers implement, plus the Phase 3
Qdrant hybrid implementation.

The `Retriever` protocol is the stable contract from Phase 0 — the graph's
research nodes only ever see it. Phase 3 swapped the in-memory BM25
placeholder for `QdrantHybridRetriever` (dense + sparse prefetch → RRF →
cross-encoder rerank) without touching the graph.
"""

from typing import Protocol, runtime_checkable

from fie.state import EvidenceChunk


@runtime_checkable
class Retriever(Protocol):
    """Anything that maps a query to scored evidence chunks."""

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        """Return the top-`k` chunks for `query`, best first, with `score` set."""
        ...


from fie.tools.retrieval.qdrant import (  # noqa: E402
    QdrantHybridRetriever,
    chunk_point_id,
    ensure_collection,
    upsert_chunks,
)
from fie.tools.retrieval.rerank import (  # noqa: E402
    NoopReranker,
    Reranker,
    VoyageReranker,
    get_reranker,
)
from fie.tools.retrieval.sparse import Bm25SparseEncoder, SparseEncoder, SparseVector  # noqa: E402

__all__ = [
    "Bm25SparseEncoder",
    "NoopReranker",
    "QdrantHybridRetriever",
    "Reranker",
    "Retriever",
    "SparseEncoder",
    "SparseVector",
    "VoyageReranker",
    "chunk_point_id",
    "ensure_collection",
    "get_reranker",
    "upsert_chunks",
]
