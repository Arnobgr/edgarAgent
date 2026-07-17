# ADR 0001 — Model routing defaults

**Date:** 2026-07-17 · **Phase:** 0 · **Status:** accepted

## Decision

Roles, not models, are the unit of routing. Every LLM call site asks
`fie.llm.get_model(role)`; the role→model mapping lives in `fie.config.Settings`
and nowhere else.

Defaults (from `plans/00-overview.md` §4):

| Role | Model | Path |
|---|---|---|
| `synthesis`, `critic` | `claude-opus-4-8` (adaptive thinking) | Anthropic via `langchain-anthropic` |
| `decompose`, `claim_extract`, `verify_qualitative`, `verify_forward` | `google/gemini-2.5-flash` | OpenRouter (OpenAI-compatible endpoint) |

Two hard rules, enforced in the router:

1. **Anthropic models never go through the OpenRouter/OpenAI-compatible path.**
   They are called through Anthropic's native SDK integration only; the router
   raises if configuration tries otherwise.
2. **Model IDs are never hardcoded at call sites.** Routing experiments are
   one-line config changes, and every call is tagged with its role
   (`fie-role:<role>`) so traces attribute cost per role.

## Rationale

High-stakes steps (the draft answer, the quality gate) get the frontier model;
high-volume mechanical steps get a flash-class model at a fraction of the cost
(blueprint §10). Opus 4.8 runs with adaptive thinking (`{"type": "adaptive"}` —
the `budget_tokens` form is rejected on this model family) and without sampling
params, per current Anthropic API requirements.

## Revisit

Phase 7 reruns the eval set with synthesis on `claude-sonnet-5`; if metrics
hold, the cheaper routing becomes the default. Verifier roles escalate to the
frontier model only where Phase 3's measured precision/recall demands it.
