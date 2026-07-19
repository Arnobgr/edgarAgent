"""Cross-encoder reranking over the fused candidate list (workstream B.2).

Primary: Voyage `rerank-2.5` — one vendor with the embeddings if Voyage
wins the benchmark. `NoopReranker` keeps the RRF order (the rerank-off arm
of the eval, and the fallback when no key is configured). A local
`bge-reranker-v2-m3` fallback is documented in the README; it is not
shipped as code to keep the dependency set lean.
"""

import logging
import time
from typing import Protocol, runtime_checkable

import httpx

from fie.config import Settings

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0


@runtime_checkable
class Reranker(Protocol):
    """Anything that reorders candidate documents for a query."""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """Return (candidate_index, relevance_score) pairs, best first."""
        ...


class NoopReranker:
    """Keeps the fused order; scores are inverse-rank placeholders."""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        del query
        return [(i, 1.0 / (i + 1)) for i in range(min(top_k, len(documents)))]


class VoyageReranker:
    """Hosted cross-encoder: Voyage rerank-2.5."""

    def __init__(self, api_key: str, model: str = "rerank-2.5") -> None:
        if not api_key:
            raise RuntimeError("missing VOYAGE_API_KEY for the reranker — add it to .env")
        self.model = model
        self._client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=60.0)

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_k": min(top_k, len(documents)),
        }
        for attempt in range(_MAX_ATTEMPTS):
            response = self._client.post("https://api.voyageai.com/v1/rerank", json=payload)
            if response.status_code == 429 and attempt < _MAX_ATTEMPTS - 1:
                delay = _BACKOFF_BASE_SECONDS * 2**attempt
                logger.warning("rerank rate-limited; retrying in %.0fs", delay)
                time.sleep(delay)
                continue
            response.raise_for_status()
            results = response.json()["data"]
            return [(r["index"], float(r["relevance_score"])) for r in results]
        raise RuntimeError("unreachable")  # pragma: no cover


def get_reranker(settings: Settings, provider: str | None = None) -> Reranker:
    """The configured reranker (default: settings choice; "none" → RRF order)."""
    provider = provider or settings.reranker_provider
    if provider == "voyage":
        return VoyageReranker(settings.voyage_api_key.get_secret_value(), settings.reranker_model)
    if provider == "none":
        return NoopReranker()
    raise KeyError(f"unknown reranker provider {provider!r}")
