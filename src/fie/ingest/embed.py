"""Dense embedding clients (workstream A.5 / B.1).

Two candidates, benchmarked head-to-head on the retrieval eval set
(decision 3.4): `voyage-finance-2` (finance-tuned) vs OpenAI
`text-embedding-3-small` (general baseline). Thin httpx REST clients — no
vendor SDKs — with batching and 429 backoff. Voyage distinguishes
document vs query embeddings (`input_type`); OpenAI does not.
"""

import logging
import time
from typing import Any, Literal, Protocol, cast, runtime_checkable

import httpx

from fie.config import Settings

logger = logging.getLogger(__name__)

InputType = Literal["document", "query"]

_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0


@runtime_checkable
class Embedder(Protocol):
    """Anything that maps texts to dense vectors."""

    model: str
    dim: int

    def embed(self, texts: list[str], input_type: InputType = "document") -> list[list[float]]:
        """Return one vector per text, in order."""
        ...


class _RestEmbedder:
    """Shared batching/retry loop over a JSON embeddings endpoint."""

    def __init__(self, *, url: str, api_key: str, model: str, dim: int, batch_size: int) -> None:
        if not api_key:
            raise RuntimeError(f"missing API key for embedding model {model!r} — add it to .env")
        self.model = model
        self.dim = dim
        self._url = url
        self._batch_size = batch_size
        self._client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=120.0)

    def embed(self, texts: list[str], input_type: InputType = "document") -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            data = self._post(self._payload(batch, input_type))
            rows = sorted(cast(list[dict[str, Any]], data["data"]), key=lambda d: int(d["index"]))
            vectors.extend([cast(list[float], row["embedding"]) for row in rows])
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"embedding count mismatch: {len(vectors)} vectors for {len(texts)} texts"
            )
        return vectors

    def _payload(self, batch: list[str], input_type: InputType) -> dict[str, object]:
        raise NotImplementedError

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        for attempt in range(_MAX_ATTEMPTS):
            response = self._client.post(self._url, json=payload)
            if response.status_code == 429 and attempt < _MAX_ATTEMPTS - 1:
                delay = _BACKOFF_BASE_SECONDS * 2**attempt
                logger.warning("embeddings rate-limited; retrying in %.0fs", delay)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return dict(response.json())
        raise RuntimeError("unreachable")  # pragma: no cover


class VoyageEmbedder(_RestEmbedder):
    """voyage-finance-2 — finance-domain embeddings, 1024 dims."""

    def __init__(self, api_key: str, model: str = "voyage-finance-2", dim: int = 1024) -> None:
        super().__init__(
            url="https://api.voyageai.com/v1/embeddings",
            api_key=api_key,
            model=model,
            dim=dim,
            batch_size=128,
        )

    def _payload(self, batch: list[str], input_type: InputType) -> dict[str, object]:
        return {"model": self.model, "input": batch, "input_type": input_type}


class OpenAIEmbedder(_RestEmbedder):
    """text-embedding-3-small — the general-purpose benchmark baseline."""

    def __init__(
        self, api_key: str, model: str = "text-embedding-3-small", dim: int = 1536
    ) -> None:
        super().__init__(
            url="https://api.openai.com/v1/embeddings",
            api_key=api_key,
            model=model,
            dim=dim,
            batch_size=256,
        )

    def _payload(self, batch: list[str], input_type: InputType) -> dict[str, object]:
        del input_type  # OpenAI embeddings are symmetric
        return {"model": self.model, "input": batch}


def get_embedder(settings: Settings, provider: str | None = None) -> Embedder:
    """The configured embedder for a provider (default: settings choice)."""
    provider = provider or settings.embedding_provider
    model, dim = settings.embedding_models[provider]
    if provider == "voyage":
        return VoyageEmbedder(settings.voyage_api_key.get_secret_value(), model, dim)
    if provider == "openai":
        return OpenAIEmbedder(settings.openai_api_key.get_secret_value(), model, dim)
    raise KeyError(f"unknown embedding provider {provider!r}")
