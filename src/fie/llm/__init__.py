"""Reasoning substrate: the role-based model router.

`get_model(role)` is the only way the rest of the codebase obtains a chat
model. Model IDs come from `fie.config`, never from call sites. Anthropic
models are always called through `langchain-anthropic` (native SDK path);
mechanical roles go through OpenRouter's OpenAI-compatible endpoint. An
Anthropic model is never routed through the OpenRouter path.
"""

from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from fie.config import Settings, get_settings

Role = Literal[
    "synthesis",
    "critic",
    "decompose",
    "claim_extract",
    "verify_qualitative",
    "verify_forward",
]

_ANTHROPIC_MARKERS = ("claude", "anthropic")


def get_model(role: Role, settings: Settings | None = None) -> BaseChatModel:
    """Return the configured chat model for a role.

    Every model is tagged with its role so the Langfuse trace shows which
    role made each LLM call.
    """
    s = settings or get_settings()
    tags = [f"fie-role:{role}"]
    metadata = {"fie_role": role}

    if role in s.anthropic_models:
        return ChatAnthropic(
            model_name=s.anthropic_models[role],
            max_tokens_to_sample=s.synthesis_max_tokens,
            thinking={"type": "adaptive"},
            api_key=s.anthropic_api_key,
            timeout=None,
            stop=None,
            tags=tags,
            metadata=metadata,
        )

    if role in s.openrouter_models:
        model_id = s.openrouter_models[role]
        if any(marker in model_id.lower() for marker in _ANTHROPIC_MARKERS):
            raise ValueError(
                f"Role {role!r} maps to Anthropic model {model_id!r} but is configured "
                "for the OpenRouter path. Anthropic models must go through the native "
                "SDK path: move the role to Settings.anthropic_models."
            )
        return ChatOpenAI(
            model=model_id,
            base_url=s.openrouter_base_url,
            api_key=s.openrouter_api_key or SecretStr(""),
            temperature=0.0,
            tags=tags,
            metadata=metadata,
        )

    raise KeyError(f"Unknown model role: {role!r}. Add it to Settings model routing.")
