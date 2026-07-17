import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from fie.config import Settings
from fie.llm import get_model


def test_synthesis_routes_to_anthropic_with_configured_id(settings: Settings) -> None:
    model = get_model("synthesis", settings)

    assert isinstance(model, ChatAnthropic)
    assert model.model == settings.anthropic_models["synthesis"]
    assert model.thinking == {"type": "adaptive"}
    assert model.tags == ["fie-role:synthesis"]


def test_mechanical_role_routes_to_openrouter(settings: Settings) -> None:
    model = get_model("decompose", settings)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == settings.openrouter_models["decompose"]
    assert model.openai_api_base == settings.openrouter_base_url
    assert model.tags == ["fie-role:decompose"]


def test_anthropic_model_never_routes_through_openrouter() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        openrouter_api_key=SecretStr("sk-or-test"),
        openrouter_models={"decompose": "anthropic/claude-haiku-4-5"},
    )
    with pytest.raises(ValueError, match="native"):
        get_model("decompose", settings)


def test_unknown_role_raises() -> None:
    settings = Settings(_env_file=None, anthropic_models={}, openrouter_models={})
    with pytest.raises(KeyError, match="synthesis"):
        get_model("synthesis", settings)
