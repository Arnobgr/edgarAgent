"""Central configuration (pydantic-settings). All env/config lives here.

Model IDs are config, never hardcoded at call sites (blueprint §10): the
router in `fie.llm` resolves a role to a model through this module only.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PHASE = "phase-3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- API keys (from .env / environment) ---
    anthropic_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    voyage_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")  # embeddings benchmark baseline only

    # --- Langfuse (self-hosted; keys generated in its UI after first boot) ---
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "http://localhost:3000"

    # --- Model routing (plans/00-overview.md §4) ---
    # Roles served by Anthropic models (native SDK path, adaptive thinking).
    anthropic_models: dict[str, str] = Field(
        default={
            "synthesis": "claude-opus-4-8",
            "critic": "claude-opus-4-8",
            # Citation-accuracy LLM judge (eval only, blueprint §6).
            "eval_judge": "claude-opus-4-8",
        }
    )
    # Mechanical roles served by a cheap model via OpenRouter.
    openrouter_models: dict[str, str] = Field(
        default={
            "decompose": "google/gemini-2.5-flash",
            "claim_extract": "google/gemini-2.5-flash",
            "verify_qualitative": "google/gemini-2.5-flash",
            "verify_forward": "google/gemini-2.5-flash",
            # Claim text → candidate XBRL (concept, period, unit) triples.
            "fact_mapping": "google/gemini-2.5-flash",
            # Retrieval-eval query seeding (candidates for hand-verification).
            "eval_seed": "google/gemini-2.5-flash",
        }
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    synthesis_max_tokens: int = 16000

    # --- EDGAR (SEC requires a declared identity in the user agent) ---
    # Personal data stays out of the repo: set EDGAR_IDENTITY in .env
    # ("Firstname Lastname email@example.com"). Fetches fail fast if unset.
    edgar_identity: str = ""
    data_dir: Path = Path("data")

    # --- Corpus (decision 3.1: TSLA/AAPL/NVDA, EDGAR-only, last 3 FYs) ---
    corpus_tickers: list[str] = Field(default=["TSLA", "AAPL", "NVDA"])
    ingest_years: int = 3
    # Phase 2 naive-corpus knob, still used by `fetch_recent_filings`.
    quarterly_filings: int = 4

    # --- Ingestion (Phase 3, workstream A) ---
    # Manifest + XBRL facts store; raw/parsed/chunk caches live under data_dir.
    ingest_db_name: str = "ingest.db"
    chunk_target_tokens: int = 768  # blueprint §5: 512-1024 with ~15% overlap
    chunk_overlap_ratio: float = 0.15
    table_max_tokens: int = 2000  # tables atomic; row-group split beyond this

    # --- Retrieval (Phase 3, workstream B: Qdrant hybrid + rerank) ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_prefix: str = "fie_chunks"
    # Embedding candidates for the benchmark (decision 3.4); the provider in
    # use is picked by measurement on the retrieval eval set.
    embedding_provider: str = "voyage"  # "voyage" | "openai"
    embedding_models: dict[str, tuple[str, int]] = Field(
        default={
            "voyage": ("voyage-finance-2", 1024),
            "openai": ("text-embedding-3-small", 1536),
        }
    )
    reranker_provider: str = "voyage"  # "voyage" | "none"
    reranker_model: str = "rerank-2.5"
    retrieval_k: int = 8
    retrieval_prefetch: int = 50  # per-vector prefetch depth before RRF
    retrieval_candidates: int = 50  # fused candidates handed to the reranker

    # --- Fan-out (Phase 2) ---
    max_sub_questions: int = 5
    max_concurrent_research: int = 5
    # Fan-in chunk budget per sub-question (context packing at synthesis).
    chunks_per_sub_question: int = 8

    # --- Guardrails (Phase 1) ---
    # Iteration caps count node passes: 2 verify passes = 1 loop-back.
    max_verify_iterations: int = 2
    max_critic_iterations: int = 2
    node_timeout_seconds: float = 300.0
    llm_retry_attempts: int = 3
    llm_retry_base_delay_seconds: float = 1.0
    budget_max_tokens: int = 500_000
    budget_max_cost_usd: float = 5.0
    # USD per million input/output tokens, matched by substring against the
    # model id reported in callbacks; unmatched models cost $0 (tokens still
    # count against the token cap).
    model_prices: dict[str, tuple[float, float]] = Field(
        default={
            "claude-opus-4-8": (5.0, 25.0),
            "claude-sonnet-5": (3.0, 15.0),
            "gemini-2.5-flash": (0.30, 2.50),
        }
    )

    @property
    def ingest_db_path(self) -> Path:
        return self.data_dir / self.ingest_db_name

    def qdrant_collection(self, provider: str | None = None) -> str:
        """Collection name for an embedding provider's model — one collection
        per embedding model so the benchmark can hold both side by side."""
        model, _dim = self.embedding_models[provider or self.embedding_provider]
        return f"{self.qdrant_collection_prefix}__{model.replace('/', '-')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
