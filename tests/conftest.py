from pathlib import Path

import pytest
from pydantic import SecretStr

from fie.config import Settings

API_KEY = "test-api-key"
# A loopback port nothing binds. The health check and the Turnstile
# siteverify call both go out over HTTP, and pointing them here makes them
# fail fast and *offline* rather than reaching whatever happens to be running
# on the developer's machine.
CLOSED_URL = "http://127.0.0.1:1"


@pytest.fixture
def settings() -> Settings:
    """Settings with dummy keys, isolated from .env and the environment."""
    return Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        openrouter_api_key=SecretStr("sk-or-test"),
    )


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    """Settings for the HTTP surface: a real key, an isolated `data_dir` so
    `api.db` and `checkpoints.db` never touch the developer's, and no
    reachable network."""
    return Settings(
        _env_file=None,
        api_key=SecretStr(API_KEY),
        data_dir=tmp_path,
        qdrant_url=CLOSED_URL,
        turnstile_verify_url=f"{CLOSED_URL}/siteverify",
    )
