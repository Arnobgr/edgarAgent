import json
from typing import Any

import httpx
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from fie.config import ModelSpec, Settings
from fie.guardrails import ainvoke_with_retry
from fie.llm import bind_structured, get_model
from fie.state import SynthesisOutput


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


def openrouter_answering(args: dict[str, Any], requests: list[dict[str, Any]]) -> ChatOpenAI:
    """A ChatOpenAI whose every call returns one tool call carrying `args`,
    over a mock transport — the real parse path, offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        tool_call = {
            "id": "call_82b7f963fe1c4f67b9b3f7bb",
            "type": "function",
            "function": {
                "name": body["tools"][0]["function"]["name"],
                "arguments": json.dumps(args),
            },
        }
        message = {"role": "assistant", "content": "", "tool_calls": [tool_call]}
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "object": "chat.completion",
                "created": 0,
                "model": "z-ai/glm-5.2",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
            },
        )

    return ChatOpenAI(
        model="z-ai/glm-5.2-20260616",
        api_key=SecretStr("sk-or-test"),
        base_url="http://openrouter.invalid/v1",
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_a_draft_that_omits_claims_is_re_asked_not_read_as_empty() -> None:
    """ADR 0046. The exact shape a GLM backend returned for g-007 on
    2026-10-10: a correct, cited answer and no `claims` key. While the field
    had a default it parsed as `claims=[]` and the run degraded; required, it
    is a validation error and the bounded retry asks again."""
    requests: list[dict[str, Any]] = []
    model = openrouter_answering({"answer": "Operating income was $4,355 million [c1]."}, requests)

    with pytest.raises(ValidationError, match="claims"):
        await ainvoke_with_retry(
            bind_structured(model, SynthesisOutput), "q", base_delay_seconds=0.0
        )

    assert len(requests) == 3
    assert "claims" in requests[0]["tools"][0]["function"]["parameters"]["required"]


async def test_an_explicit_empty_claims_list_still_parses() -> None:
    """Required is not non-empty: a model that says "no claims" in so many
    words must still reach ADR 0038's repair and failure, which is where an
    unverifiable answer is decided."""
    requests: list[dict[str, Any]] = []
    model = openrouter_answering({"answer": "Nothing to state.", "claims": []}, requests)

    result = await ainvoke_with_retry(bind_structured(model, SynthesisOutput), "q")

    assert result == SynthesisOutput(answer="Nothing to state.", claims=[])
    assert len(requests) == 1


CLAIM = {
    "text": "Revenue was $94,827 million.",
    "claim_type": "numeric",
    "source_chunk_ids": ["c1"],
    "answer_span": "Revenue was $94,827 million.",
}


@pytest.mark.parametrize("omitted", ["source_chunk_ids", "answer_span"])
async def test_a_claim_that_omits_a_field_is_re_asked_not_read_as_empty(omitted: str) -> None:
    """ADR 0046, addendum. The same backend that dropped `claims` dropped
    `source_chunk_ids` from every claim of g-014 on 2026-10-10, so six correct
    figures were ruled to cite nothing and retracted, and dropped `answer_span`
    from g-002, whose prose could then not be highlighted."""
    requests: list[dict[str, Any]] = []
    claim = {k: v for k, v in CLAIM.items() if k != omitted}
    model = openrouter_answering(
        {"answer": "Revenue was $94,827 million.", "claims": [claim]}, requests
    )

    with pytest.raises(ValidationError, match=omitted):
        await ainvoke_with_retry(
            bind_structured(model, SynthesisOutput), "q", base_delay_seconds=0.0
        )

    assert len(requests) == 3
    claims_schema = requests[0]["tools"][0]["function"]["parameters"]["properties"]["claims"]
    assert omitted in claims_schema["items"]["required"]


async def test_explicitly_empty_claim_fields_still_parse() -> None:
    """Required is not non-empty: an explicit `[]` still reaches verify's
    "cites no evidence" ruling, and an explicit `""` still reaches the
    token-overlap fallback (ADR 0023), exactly as before."""
    requests: list[dict[str, Any]] = []
    claim = {**CLAIM, "source_chunk_ids": [], "answer_span": ""}
    model = openrouter_answering(
        {"answer": "Revenue was $94,827 million.", "claims": [claim]}, requests
    )

    result = await ainvoke_with_retry(bind_structured(model, SynthesisOutput), "q")

    assert result.claims[0].source_chunk_ids == []
    assert result.claims[0].answer_span == ""
    assert len(requests) == 1
