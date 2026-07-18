"""Central configuration (pydantic-settings). All env/config lives here.

Model IDs are config, never hardcoded at call sites (blueprint §10): the
router in `fie.llm` resolves a role to a model through this module only.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PHASE = "phase-1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- API keys (from .env / environment) ---
    anthropic_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")

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
        }
    )
    # Mechanical roles served by a cheap model via OpenRouter.
    openrouter_models: dict[str, str] = Field(
        default={
            "decompose": "google/gemini-2.5-flash",
            "claim_extract": "google/gemini-2.5-flash",
            "verify_qualitative": "google/gemini-2.5-flash",
            "verify_forward": "google/gemini-2.5-flash",
        }
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    synthesis_max_tokens: int = 16000

    # --- EDGAR (SEC requires a declared identity in the user agent) ---
    edgar_identity: str = "Firstname Lastname email@example.com"
    data_dir: Path = Path("data")

    # --- Retrieval ---
    retrieval_k: int = 6

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
