"""Retrieval: the interface all retrievers implement, plus the Phase 0
in-memory BM25 placeholder.

The `Retriever` protocol is the stable contract: Phase 3's Qdrant hybrid
retrieval implements the same interface, so swapping retrievers never
touches the graph.
"""

import re
from typing import Protocol, runtime_checkable

from rank_bm25 import BM25Okapi

from fie.state import EvidenceChunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Retriever(Protocol):
    """Anything that maps a query to scored evidence chunks."""

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        """Return the top-`k` chunks for `query`, best first, with `score` set."""
        ...


class BM25Retriever:
    """Naive in-memory BM25 over pre-chunked evidence (Phase 0 placeholder)."""

    def __init__(self, chunks: list[EvidenceChunk]) -> None:
        if not chunks:
            raise ValueError("BM25Retriever needs at least one chunk")
        self._chunks = chunks
        self._bm25 = BM25Okapi([_tokenize(c.text) for c in chunks])

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(self._chunks)), key=lambda i: scores[i], reverse=True)[:k]
        return [self._chunks[i].model_copy(update={"score": float(scores[i])}) for i in ranked]


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())
