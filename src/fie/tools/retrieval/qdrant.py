"""Qdrant hybrid retrieval (workstream B): named dense + sparse vectors,
server-side RRF fusion, then cross-encoder reranking.

One collection per embedding model (the benchmark holds both side by side).
Every chunk's full citation metadata rides in the payload; `ticker`,
`form_type`, `fiscal_period`, `fiscal_year` and `chunk_type` are payload-
indexed so research branches can filter by company/period when the
sub-question implies it — inferred here from the query text, conservatively
(a filter is only applied when exactly one corpus ticker, or one explicit
period, is named).
"""

import logging
import re
import uuid
from typing import Any

from qdrant_client import QdrantClient, models

from fie.ingest.embed import Embedder
from fie.state import EvidenceChunk
from fie.tools.retrieval.rerank import Reranker
from fie.tools.retrieval.sparse import SparseEncoder, SparseVector

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "sparse"

_INDEXED_KEYWORD_FIELDS = ("ticker", "form_type", "fiscal_period", "chunk_type")

# Company-name aliases for query-implied ticker filtering (corpus 3.1).
_TICKER_ALIASES = {
    "TSLA": ("tsla", "tesla"),
    "AAPL": ("aapl", "apple"),
    "NVDA": ("nvda", "nvidia"),
}

_QUARTER_RE = re.compile(
    r"\b(?:q([1-4])\s*(?:of\s*)?((?:20)\d{2})|((?:20)\d{2})\s*q([1-4]))\b", re.I
)
_FISCAL_YEAR_RE = re.compile(r"\b(?:fy\s*|fiscal\s+(?:year\s+)?)((?:20)\d{2})\b", re.I)


def chunk_point_id(chunk_id: str) -> str:
    """Deterministic UUID for a chunk id (Qdrant point ids must be UUID/int)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fie://chunk/{chunk_id}"))


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Create the collection (named vectors + payload indexes) if missing."""
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field in _INDEXED_KEYWORD_FIELDS:
        client.create_payload_index(
            collection_name=name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
        )
    client.create_payload_index(
        collection_name=name,
        field_name="fiscal_year",
        field_schema=models.PayloadSchemaType.INTEGER,
    )
    logger.info("created Qdrant collection %s (dense dim %d + sparse BM25)", name, dim)


def _payload(chunk: EvidenceChunk) -> dict[str, Any]:
    year = chunk.metadata.get("fiscal_year", "")
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "ticker": chunk.ticker,
        "form_type": chunk.form_type,
        "section": chunk.section,
        "text": chunk.text,
        "fiscal_period": chunk.metadata.get("fiscal_period", ""),
        "fiscal_year": int(year) if year.isdigit() else None,
        "chunk_type": chunk.metadata.get("chunk_type", "text"),
        "metadata": dict(chunk.metadata),
    }


def _to_chunk(payload: dict[str, Any], score: float) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=payload["chunk_id"],
        doc_id=payload["doc_id"],
        ticker=payload["ticker"],
        form_type=payload["form_type"],
        section=payload["section"],
        text=payload["text"],
        score=score,
        metadata={k: str(v) for k, v in payload.get("metadata", {}).items()},
    )


def upsert_chunks(
    client: QdrantClient,
    collection: str,
    chunks: list[EvidenceChunk],
    dense_vectors: list[list[float]],
    sparse_vectors: list[SparseVector],
    *,
    batch_size: int = 128,
) -> None:
    """Upsert chunks with both named vectors; idempotent via stable ids."""
    if not (len(chunks) == len(dense_vectors) == len(sparse_vectors)):
        raise ValueError("chunks / dense / sparse lengths differ")
    for start in range(0, len(chunks), batch_size):
        points = [
            models.PointStruct(
                id=chunk_point_id(chunk.chunk_id),
                vector={
                    DENSE: dense,
                    SPARSE: models.SparseVector(indices=sv.indices, values=sv.values),
                },
                payload=_payload(chunk),
            )
            for chunk, dense, sv in zip(
                chunks[start : start + batch_size],
                dense_vectors[start : start + batch_size],
                sparse_vectors[start : start + batch_size],
                strict=True,
            )
        ]
        client.upsert(collection_name=collection, points=points)


def infer_filter(query: str, corpus_tickers: list[str]) -> models.Filter | None:
    """Query-implied payload filter: exactly one named company → ticker
    filter; one explicit quarter or fiscal year → period filter. Ambiguous
    or absent mentions (e.g. cross-company comparisons) apply no filter."""
    text = query.lower()
    mentioned = [
        t
        for t in corpus_tickers
        if any(
            re.search(rf"\b{re.escape(alias)}\b", text)
            for alias in _TICKER_ALIASES.get(t.upper(), (t.lower(),))
        )
    ]
    must: list[models.Condition] = []
    if len(mentioned) == 1:
        must.append(
            models.FieldCondition(key="ticker", match=models.MatchValue(value=mentioned[0].upper()))
        )

    quarters = _QUARTER_RE.findall(query)
    fiscal_years = _FISCAL_YEAR_RE.findall(query)
    if len(quarters) == 1:
        q = quarters[0][0] or quarters[0][3]
        year = quarters[0][1] or quarters[0][2]
        must.append(
            models.FieldCondition(
                key="fiscal_period", match=models.MatchValue(value=f"Q{q} {year}")
            )
        )
    elif len(fiscal_years) == 1:
        must.append(
            models.FieldCondition(
                key="fiscal_year", match=models.MatchValue(value=int(fiscal_years[0]))
            )
        )
    return models.Filter(must=must) if must else None


class QdrantHybridRetriever:
    """Hybrid retrieval: dense + sparse prefetch → RRF fusion (server-side)
    → top candidates → cross-encoder rerank → top-k to the agent."""

    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        *,
        embedder: Embedder,
        sparse_encoder: SparseEncoder,
        reranker: Reranker,
        corpus_tickers: list[str],
        prefetch_limit: int = 50,
        candidates: int = 50,
    ) -> None:
        self._client = client
        self._collection = collection
        self._embedder = embedder
        self._sparse = sparse_encoder
        self._reranker = reranker
        self._corpus_tickers = corpus_tickers
        self._prefetch_limit = prefetch_limit
        self._candidates = candidates

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        dense = self._embedder.embed([query], input_type="query")[0]
        sparse = self._sparse.encode_query(query)
        flt = infer_filter(query, self._corpus_tickers)
        response = self._client.query_points(
            collection_name=self._collection,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE, limit=self._prefetch_limit, filter=flt),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE,
                    limit=self._prefetch_limit,
                    filter=flt,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=self._candidates,
            with_payload=True,
        )
        fused = [
            _to_chunk(point.payload, float(point.score))
            for point in response.points
            if point.payload is not None
        ]
        if not fused:
            return []
        ranked = self._reranker.rerank(query, [c.text for c in fused], top_k=k)
        return [fused[i].model_copy(update={"score": score}) for i, score in ranked[:k]]
