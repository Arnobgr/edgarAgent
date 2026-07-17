import pytest
from pydantic import SecretStr

from fie.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings with dummy keys, isolated from .env and the environment."""
    return Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        openrouter_api_key=SecretStr("sk-or-test"),
    )
