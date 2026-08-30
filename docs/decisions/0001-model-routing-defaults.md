# ADR 0001 — Model routing defaults

**Date:** 2026-07-17 · **Phase:** 0 · **Status:** accepted (the `synthesis`/`critic`
default below is superseded by ADR 0045 — GLM 5.2 — since the Phase 7 cost A/B closed)

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

**Resolved (ADR 0045, 2026-08-30).** The Phase 7 cost A/B ran and closed on
`z-ai/glm-5.2` for `synthesis`/`critic`, not `claude-sonnet-5`: GLM 5.2 held the
gold-set floors at a fraction of the Anthropic price ($0.95/$2.98 per M vs
Opus $5/$25, Sonnet $3/$15), so the cheaper routing became the default. The
planned live Sonnet experiment is dropped. `eval_judge` stays on `claude-sonnet-5`
(the measuring stick). Verifier roles escalate to the frontier model only where
Phase 3's measured precision/recall demands it.
