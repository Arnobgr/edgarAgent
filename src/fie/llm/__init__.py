"""Reasoning substrate: the role-based model router.

`get_model(role)` is the only way the rest of the codebase obtains a chat
model. Each role resolves to a `ModelSpec` in `fie.config` — never a model id
at the call site — and the spec's `provider` selects the client path:
Anthropic goes through `langchain-anthropic` (native SDK, adaptive thinking),
OpenRouter through its OpenAI-compatible endpoint. Any role can move between
the two by editing its spec; an Anthropic model is never routed through the
OpenRouter path (ADR 0014).
"""

from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from fie.config import ModelSpec, Settings, get_settings

Role = Literal[
    "synthesis",
    "critic",
    "decompose",
    "claim_extract",
    "verify_qualitative",
    "verify_forward",
    "fact_mapping",
    "eval_judge",
    "eval_seed",
]

_ANTHROPIC_MARKERS = ("claude", "anthropic")


def bind_structured(model: BaseChatModel, schema: type[BaseModel]) -> Runnable:
    """Attach a Pydantic output schema, picking the extraction method by
    provider so structured output is robust across both paths.

    OpenRouter models (the ChatOpenAI path) use **tool-calling**: some open
    models (observed on GLM 5.2) intermittently wrap `json_schema` output in a
    ```json markdown fence that the OpenAI SDK's strict parser rejects, which
    surfaces as flaky mid-run failures. Tool-calling returns structured
    arguments with no free-text JSON to fence.

    Anthropic models with adaptive thinking on use **json_schema**: the
    default path is forced tool calling, and `langchain-anthropic` itself
    warns that this "is not guaranteed when `thinking` is enabled" and will
    raise `OutputParserException` when the model answers in text instead of
    calling the tool. Every reasoning role here (synthesis, critic, the
    judges) is both structured *and* thinking-enabled, so that warned-against
    combination was on by default for the whole Anthropic path. json_schema
    keeps thinking — the reason those roles are on Anthropic at all — while
    removing the tool-call requirement. Anthropic roles with thinking
    disabled keep the default, which is reliable without it.
    """
    if isinstance(model, ChatOpenAI):
        return model.with_structured_output(schema, method="function_calling")
    if isinstance(model, ChatAnthropic) and _thinking_enabled(model):
        return model.with_structured_output(schema, method="json_schema")
    return model.with_structured_output(schema)


def _thinking_enabled(model: ChatAnthropic) -> bool:
    """True when the model was built with adaptive thinking (see `_anthropic_model`)."""
    thinking = getattr(model, "thinking", None)
    return isinstance(thinking, dict) and thinking.get("type") not in (None, "disabled")


def get_model(role: Role, settings: Settings | None = None) -> BaseChatModel:
    """Return the configured chat model for a role.

    Every model is tagged with its role so the Langfuse trace shows which
    role made each LLM call.
    """
    s = settings or get_settings()
    try:
        spec = s.models[role]
    except KeyError:
        raise KeyError(f"Unknown model role: {role!r}. Add it to Settings.models.") from None

    tags = [f"fie-role:{role}"]
    metadata = {"fie_role": role}
    max_tokens = spec.max_tokens or s.default_max_tokens

    if spec.provider == "anthropic":
        return _anthropic_model(spec, max_tokens, s, tags, metadata)
    if spec.provider == "openrouter":
        return _openrouter_model(role, spec, max_tokens, s, tags, metadata)
    raise ValueError(f"Unknown provider {spec.provider!r} for role {role!r}.")


def _anthropic_model(
    spec: ModelSpec,
    max_tokens: int,
    s: Settings,
    tags: list[str],
    metadata: dict[str, str],
) -> ChatAnthropic:
    # Adaptive thinking is the default and precludes sampling params (the API
    # 400s on temperature while thinking is on). Disabling thinking is the only
    # way to honor a role's temperature on the native path.
    kwargs: dict[str, object] = {}
    if spec.thinking:
        kwargs["thinking"] = {"type": "adaptive"}
    else:
        kwargs["thinking"] = {"type": "disabled"}
        if spec.temperature is not None:
            kwargs["temperature"] = spec.temperature
    return ChatAnthropic(
        model=spec.model,
        max_tokens=max_tokens,
        api_key=s.anthropic_api_key,
        timeout=None,
        stop=None,
        tags=tags,
        metadata=metadata,
        **kwargs,
    )


def _openrouter_model(
    role: Role,
    spec: ModelSpec,
    max_tokens: int,
    s: Settings,
    tags: list[str],
    metadata: dict[str, str],
) -> ChatOpenAI:
    if any(marker in spec.model.lower() for marker in _ANTHROPIC_MARKERS):
        raise ValueError(
            f"Role {role!r} maps to Anthropic model {spec.model!r} but is configured "
            "for the OpenRouter path. Anthropic models must go through the native "
            "SDK path: set provider='anthropic' on its ModelSpec."
        )
    # temperature None → deterministic 0.0 (the mechanical-role default);
    # max_tokens is always set so a role like synthesis never truncates on an
    # OpenRouter model's small default cap.
    return ChatOpenAI(
        model=spec.model,
        base_url=s.openrouter_base_url,
        api_key=s.openrouter_api_key or SecretStr(""),
        temperature=spec.temperature if spec.temperature is not None else 0.0,
        max_tokens=max_tokens,
        tags=tags,
        metadata=metadata,
    )
