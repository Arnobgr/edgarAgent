"""Sparse (BM25) vectors via fastembed, for Qdrant's sparse named vector.

BM25 term weights are computed locally by fastembed's `Qdrant/bm25` model;
Qdrant applies the IDF modifier server-side (set on the collection's sparse
vector config). Wrapped behind a small protocol so tests never load the
fastembed model (it downloads weights on first use).
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class SparseVector(BaseModel):
    """Indices/values pair, the wire format Qdrant expects."""

    indices: list[int]
    values: list[float]


@runtime_checkable
class SparseEncoder(Protocol):
    """Anything that produces BM25-style sparse vectors."""

    def encode_documents(self, texts: list[str]) -> list[SparseVector]: ...

    def encode_query(self, text: str) -> SparseVector: ...


class Bm25SparseEncoder:
    """fastembed `Qdrant/bm25` encoder (lazy model load)."""

    def __init__(self, model_name: str = "Qdrant/bm25") -> None:
        self._model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import SparseTextEmbedding

            self._model = SparseTextEmbedding(self._model_name)
        return self._model

    def encode_documents(self, texts: list[str]) -> list[SparseVector]:
        return [
            SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
            for e in self._load().embed(texts)
        ]

    def encode_query(self, text: str) -> SparseVector:
        embedding = next(iter(self._load().query_embed(text)))
        return SparseVector(indices=embedding.indices.tolist(), values=embedding.values.tolist())
