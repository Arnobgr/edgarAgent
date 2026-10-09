"""Central configuration (pydantic-settings). All env/config lives here.

Model IDs are config, never hardcoded at call sites (blueprint §10): the
router in `fie.llm` resolves a role to a model through this module only.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from fie.state import ReviewMode

PHASE = "phase-4"

# Where `docker compose` mounts secrets in the container. Runtime secrets are
# files, not env vars: env leaks into `docker inspect`, crash dumps, child
# processes and any library that dumps `environ` (ADR 0029). Passed to
# pydantic-settings only when the directory exists — on a dev machine it does
# not, and pydantic-settings warns about a missing secrets_dir on every run.
COMPOSE_SECRETS_DIR = Path("/run/secrets")


class ModelSpec(BaseModel):
    """How one role is served — the single knob for provider independence.

    `provider` selects the client path in `fie.llm.get_model`; the optional
    generation fields let a role tune output length and sampling without any
    code change, so swapping a role between Anthropic and an OpenRouter model
    is a config edit, not a rewrite.

    - `max_tokens`: output cap for this role. `None` falls back to
      `Settings.default_max_tokens`.
    - `temperature`: OpenRouter path only (defaults to 0.0 there). On the
      Anthropic path the API rejects sampling params while adaptive thinking
      is on, so it is honored only when `thinking=False`.
    - `thinking`: Anthropic adaptive thinking on/off; ignored on OpenRouter.
    """

    provider: Literal["anthropic", "openrouter"]
    model: str
    max_tokens: int | None = None
    temperature: float | None = None
    thinking: bool = True


class Settings(BaseSettings):
    """Every setting the system reads. Nothing else touches `os.environ`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- API keys (from .env / environment) ---
    anthropic_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    voyage_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")  # embeddings benchmark baseline only

    # --- Langfuse (self-hosted; keys generated in its UI after first boot) ---
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "http://localhost:3001"

    # --- Model routing (plans/00-overview.md §4) ---
    # One role → one ModelSpec. The router (`fie.llm.get_model`) reads only
    # this map, so any role can move between Anthropic and OpenRouter by
    # editing its spec — provider independence is a config property, not a
    # code change (ADR 0014).
    #
    # Reasoning roles (synthesis/critic) run on GLM 5.2 via OpenRouter — the
    # accepted default since the cost A/B closed on it (ADR 0045): it held the
    # gold-set floors at a fraction of the Anthropic price. The eval judge stays
    # on Anthropic (it is the measuring stick, not a production role). Mechanical
    # roles run on a cheap OpenRouter model. Any role moves provider by editing
    # its spec — nothing else changes (ADR 0014).
    models: dict[str, ModelSpec] = Field(
        default={
            # synthesis and critic run on GLM 5.2 via OpenRouter — the accepted
            # default since the cost A/B closed on it (ADR 0045).
            "synthesis": ModelSpec(provider="openrouter", model="z-ai/glm-5.2-20260616"),
            "critic": ModelSpec(provider="openrouter", model="z-ai/glm-5.2-20260616"),
            # Citation-accuracy LLM judge (eval only, blueprint §6). Kept on a
            # strong, stable model — it is the measuring stick for the exit gate.
            "eval_judge": ModelSpec(provider="anthropic", model="claude-sonnet-5"),
            "decompose": ModelSpec(provider="openrouter", model="google/gemini-3.5-flash-lite"),
            "claim_extract": ModelSpec(provider="openrouter", model="google/gemini-3.5-flash-lite"),
            "verify_qualitative": ModelSpec(
                provider="openrouter", model="google/gemini-3.5-flash-lite"
            ),
            "verify_forward": ModelSpec(
                provider="openrouter", model="google/gemini-3.5-flash-lite"
            ),
            # Claim text → candidate XBRL (concept, period, unit) triples.
            "fact_mapping": ModelSpec(provider="openrouter", model="google/gemini-3.5-flash-lite"),
            # Question → the filed figures to look up on the research path.
            # Its own role, not `fact_mapping`'s, so a Langfuse trace separates
            # what research looked up from what verify checked (ADR 0022).
            "fact_lookup": ModelSpec(provider="openrouter", model="google/gemini-3.5-flash-lite"),
            # Analyst phrasing → filing vocabulary, per research branch. Cheap
            # by necessity: the call multiplies across the parallel fan-out and
            # again on every verify loop-back (ADR 0022).
            "query_rewrite": ModelSpec(provider="openrouter", model="google/gemini-3.5-flash-lite"),
            # Retrieval-eval query seeding (candidates for hand-verification).
            "eval_seed": ModelSpec(provider="openrouter", model="google/gemini-3.5-flash-lite"),
        }
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Fallback output cap for any role whose ModelSpec leaves max_tokens unset.
    default_max_tokens: int = 16000

    # --- EDGAR (SEC requires a declared identity in the user agent) ---
    # Personal data stays out of the repo: set EDGAR_IDENTITY in .env
    # ("Firstname Lastname email@example.com"). Fetches fail fast if unset.
    edgar_identity: str = ""
    data_dir: Path = Path("data")

    # --- Corpus (decision 3.1: TSLA/AAPL/NVDA, EDGAR-only, last 3 FYs) ---
    corpus_tickers: list[str] = Field(default=["TSLA", "AAPL", "NVDA"])
    ingest_years: int = 3

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
    # Provisional benchmark winner (ADR 0011, seed labels 2026-07-19): OpenAI
    # ~ties Voyage but leads deeper recall; reranker adds ≈0 — confirm on
    # verified labels. Both collections remain resident for the re-run.
    embedding_provider: str = "openai"  # "voyage" | "openai"
    embedding_models: dict[str, tuple[str, int]] = Field(
        default={
            "voyage": ("voyage-finance-2", 1024),
            "openai": ("text-embedding-3-small", 1536),
        }
    )
    reranker_provider: str = "none"  # "voyage" | "none"
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
    # 600s: a legitimate re-synthesis over a full fan-in (103 evidence
    # chunks, 30 claims) exceeded 300s on 2026-07-28 and the abort skipped
    # retraction, turning 17 excisable claims into surviving hallucinations.
    # The budget caps remain the guard against true runaways.
    node_timeout_seconds: float = 600.0
    llm_retry_attempts: int = 3
    llm_retry_base_delay_seconds: float = 1.0
    budget_max_tokens: int = 500_000
    budget_max_cost_usd: float = 5.0
    # USD per million input/output tokens, matched by substring against the
    # model id reported in callbacks; unmatched models cost $0 (tokens still
    # count against the token cap).
    model_prices: dict[str, tuple[float, float]] = Field(
        default={
            # Opus 5 is the current Opus and is priced identically to 4.8, so
            # the challenger probe pays nothing extra for the better model.
            # Substring matching means 4-8 does NOT cover 5 — both are listed.
            "claude-opus-5": (5.0, 25.0),
            "claude-opus-4-8": (5.0, 25.0),
            # The introductory 2.0/10.0 outlived its 2026-08-31 end date and is
            # the list price as of 2026-10-06. Also covers sonnet-5-5 by
            # substring, which is priced the same.
            "claude-sonnet-5": (2.0, 10.0),
            # Priced identically to the 2.5-flash it replaced, so the
            # generation jump costs nothing. 2.5 stays listed: substring
            # matching means the newer id does not cover the older one, and
            # checkpoints from earlier runs still resolve.
            "gemini-3.5-flash-lite": (0.30, 2.50),
            "gemini-2.5-flash": (0.30, 2.50),
            # OpenRouter model prices (ADR 0014). glm-5.2 is the synthesis/critic
            # default (ADR 0045); the rest were A/B challengers, kept for cost
            # comparison. Substring-matched against the id in callbacks, e.g.
            # "z-ai/glm-5.2-20260616" → "glm-5.2". What OpenRouter charges as of
            # 2026-10-09; it was 0.95/2.98 when the 08-13 baseline ran, so that
            # run's recorded costs are at the old price.
            "glm-5.2": (0.057, 4.30),
            "grok-4.5": (2.0, 6.0),
            "qwen3.7-max": (1.48, 4.43),
            "kimi-k3": (3.0, 15.0),
        }
    )

    # --- Public API (Phase 4) ---
    # Static key for the *tailnet* consumer (CLI, the operator's Mac). On the public
    # path it identifies rather than controls: a static SPA ships its key in
    # the bundle, where devtools can read it. Turnstile and the quotas below
    # are the enforcement — see docs/security.md and ADR 0030.
    api_key: SecretStr = SecretStr("")
    # Exactly the Pages origin. The frontend and the API are on different
    # hosts, so every browser call is cross-origin; never "*".
    api_cors_origins: list[str] = Field(default=[])
    api_db_name: str = "api.db"
    api_review_mode: ReviewMode = "off"
    # JSON logs in the container, human-readable ones on a terminal.
    api_log_json: bool = False

    # Cloudflare Turnstile. Origin-agnostic, so it needs no zone and no
    # domain: the widget renders on the Pages origin and this service only
    # verifies the token. An unset secret disables verification, which is
    # what tests and local dev run.
    # Explicit rather than inferred from "is a secret set?": a control that
    # silently switches itself off when an env var goes missing is the one
    # failure mode this whole layer exists to avoid. `create_app` refuses to
    # start with this on and no secret, so "off" is always a decision.
    turnstile_required: bool = False
    turnstile_secret_key: SecretStr = SecretStr("")
    turnstile_verify_url: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    # Public-path run caps, applied by `public_run_settings()`.
    # `budget_max_cost_usd = 5.0` is ~200x the $0.023 median: a runaway guard
    # for a CLI the operator drives themselves, not a control for an endpoint strangers
    # can reach.
    public_budget_max_cost_usd: float = 0.25
    # 600s interacts badly with a 2-slot semaphore: CLAUDE.md records
    # synthesize stalling at the cap ~1.5% of the time, and one stall would
    # park half the public capacity for ten minutes.
    public_node_timeout_seconds: float = 180.0

    # Spend governor (ADR 0030). The daily sub-cap is what makes one bad day
    # cost 7% of the month instead of all of it.
    spend_cap_daily_usd: float = 0.35
    spend_cap_monthly_usd: float = 5.0
    public_daily_quota_per_ip: int = 3
    # Concurrency is the OOM control, sized to the container memory limits in
    # docker-compose.prod.yml rather than to taste.
    api_max_concurrent_runs: int = 2
    api_max_queued_runs: int = 4
    question_max_chars: int = 500

    @property
    def ingest_db_path(self) -> Path:
        return self.data_dir / self.ingest_db_name

    @property
    def api_db_path(self) -> Path:
        """Jobs, spend ledger, per-IP quota and answer cache. Separate from
        `ingest.db` (read-mostly corpus truth) and from `checkpoints.db`
        (LangGraph's), so the serving state can be backed up, pruned or
        dropped without touching either."""
        return self.data_dir / self.api_db_name

    def public_run_settings(self) -> "Settings":
        """Settings for a run submitted over the public HTTP path.

        The tailnet path keeps the loose caps: it is the operator driving their own
        machine and a stall costs them a coffee. The public path gets the
        tight ones, and they are tightened *here* rather than at the call
        site so `answer_question` stays one function with one behaviour."""
        return self.model_copy(
            update={
                "budget_max_cost_usd": self.public_budget_max_cost_usd,
                "node_timeout_seconds": self.public_node_timeout_seconds,
            }
        )

    def qdrant_collection(self, provider: str | None = None) -> str:
        """Collection name for an embedding provider's model — one collection
        per embedding model so the benchmark can hold both side by side."""
        model, _dim = self.embedding_models[provider or self.embedding_provider]
        return f"{self.qdrant_collection_prefix}__{model.replace('/', '-')}"


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings, from the compose secrets directory when it
    is mounted (production) and from `.env` otherwise."""
    if COMPOSE_SECRETS_DIR.is_dir():
        return Settings(_secrets_dir=COMPOSE_SECRETS_DIR)
    return Settings()
