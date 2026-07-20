import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from fie.config import ModelSpec, Settings
from fie.llm import get_model


def test_anthropic_role_builds_native_client_with_adaptive_thinking() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        models={"synthesis": ModelSpec(provider="anthropic", model="claude-sonnet-5")},
    )
    model = get_model("synthesis", settings)

    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-sonnet-5"
    assert model.thinking == {"type": "adaptive"}
    assert model.max_tokens == settings.default_max_tokens
    assert model.tags == ["fie-role:synthesis"]


def test_mechanical_role_routes_to_openrouter(settings: Settings) -> None:
    model = get_model("decompose", settings)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == settings.models["decompose"].model
    assert model.openai_api_base == settings.openrouter_base_url
    assert model.temperature == 0.0
    assert model.max_tokens == settings.default_max_tokens
    assert model.tags == ["fie-role:decompose"]


def test_role_can_switch_provider_via_spec() -> None:
    """A role served by Anthropic by default can move to OpenRouter with only
    a ModelSpec edit — the provider-independence property (ADR 0014)."""
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        models={"synthesis": ModelSpec(provider="openrouter", model="z-ai/glm-5.2-20260616")},
    )
    model = get_model("synthesis", settings)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "z-ai/glm-5.2-20260616"


def test_spec_max_tokens_and_temperature_override_openrouter() -> None:
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-test"),
        models={
            "synthesis": ModelSpec(
                provider="openrouter",
                model="z-ai/glm-5.2-20260616",
                max_tokens=8000,
                temperature=0.3,
            )
        },
    )
    model = get_model("synthesis", settings)

    assert model.max_tokens == 8000
    assert model.temperature == 0.3


def test_anthropic_thinking_off_enables_temperature() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        models={
            "synthesis": ModelSpec(
                provider="anthropic",
                model="claude-sonnet-5",
                thinking=False,
                temperature=0.2,
            )
        },
    )
    model = get_model("synthesis", settings)

    assert isinstance(model, ChatAnthropic)
    assert model.thinking == {"type": "disabled"}
    assert model.temperature == 0.2


def test_anthropic_model_never_routes_through_openrouter() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        openrouter_api_key=SecretStr("sk-or-test"),
        models={"decompose": ModelSpec(provider="openrouter", model="anthropic/claude-haiku-4-5")},
    )
    with pytest.raises(ValueError, match="native"):
        get_model("decompose", settings)


def test_unknown_role_raises() -> None:
    settings = Settings(_env_file=None, models={})
    with pytest.raises(KeyError, match="synthesis"):
        get_model("synthesis", settings)
