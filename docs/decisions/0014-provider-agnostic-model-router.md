# ADR 0014 — Provider-agnostic model router (per-role ModelSpec)

**Date:** 2026-07-20 · **Phase:** 3 · **Status:** accepted

## Context

Model routing (ADR 0001) split roles across two config maps — `anthropic_models`
and `openrouter_models` — and the router inferred the client path from *which
map* a role appeared in. Two roles (`synthesis`, `critic`) plus the citation
`eval_judge` ran on Anthropic; the mechanical roles ran on a cheap OpenRouter
model. Moving a role between providers meant editing two structures, and the
per-path generation knobs were hardcoded in the router: the Anthropic path
pinned adaptive thinking and `synthesis_max_tokens`; the OpenRouter path pinned
`temperature=0.0` and set **no** `max_tokens` at all.

This blocked two things we now want:

1. **Cost A/B on the reasoning roles.** We want to trial SOTA-but-cheaper open
   models (GLM 5.2, Grok 4.5, …) on `synthesis`/`critic` against the Sonnet
   baseline. The output-heavy synthesis role is where the eval spend lives.
2. **Provider independence as a design property**, so the model behind any role
   can change without touching the codebase — a config decision, not a rewrite.

## Decision

Replace the two maps with a single `models: dict[str, ModelSpec]`. Each
`ModelSpec` carries `provider` (`"anthropic" | "openrouter"`), `model`, and the
optional generation knobs `max_tokens`, `temperature`, `thinking`.
`fie.llm.get_model` reads only this map and builds the right client:

- **Anthropic path** — adaptive thinking by default. Because the API rejects
  sampling params while thinking is on, `temperature` is honored only when
  `thinking=False` (which switches thinking to `disabled`).
- **OpenRouter path** — `temperature` (default `0.0`) and `max_tokens` are
  always set. Setting `max_tokens` closes a latent truncation bug: an OpenRouter
  model previously fell back to its own small default cap, which would clip a
  16k-token synthesis response. An Anthropic model id on the OpenRouter path is
  still rejected (native SDK only).

`max_tokens` unset on a spec falls back to `Settings.default_max_tokens`
(formerly `synthesis_max_tokens`).

### Knob availability

| Knob | Anthropic | OpenRouter |
|---|---|---|
| `max_tokens` | yes | yes (now always applied) |
| `temperature` | only with `thinking=False` | yes (default 0.0) |
| `thinking` | adaptive on/off | n/a |

## Consequences

- Switching any role between providers is a one-line `ModelSpec` edit. Callers
  are unchanged: `get_model(role)` still returns a `BaseChatModel` and
  `.with_structured_output(...)` still chains.
- The `feat/router-provider-agnostic` branch flips `synthesis`/`critic` to
  `z-ai/glm-5.2-20260616` as the challenger arm; `eval_judge` stays on
  `claude-sonnet-5` as the fixed measuring stick for the exit gate. `main`/`dev`
  keep the Sonnet baseline. Challenger prices are registered in `model_prices`
  so `budget_max_cost_usd` still counts the spend. *(Resolved by ADR 0045,
  2026-08-30: the A/B closed on GLM 5.2; it is now the accepted `synthesis`/`critic`
  default on `dev`/`main`, and `eval_judge` remains on `claude-sonnet-5`.)*
- **Model choice must be frozen before the canonical baseline run** (exit-gate
  step 4): the baseline becomes the Phase-4 CI regression gate, and any later
  model swap forces a re-baseline.
- **Structured-output robustness (risk materialized, resolved):** the smoke
  test surfaced exactly the predicted failure — GLM 5.2 intermittently wraps
  `json_schema` output in a ```json markdown fence, which the OpenAI SDK's
  strict parser rejects (`ValidationError: Invalid JSON`), showing up as flaky
  mid-run failures once retries exhaust. Fixed with `fie.llm.bind_structured`,
  a provider-aware wrapper that forces `method="function_calling"` (tool-call
  output, no free-text JSON to fence) on the OpenRouter path and leaves the
  Anthropic path on its default. All structured-output binding sites route
  through it. The Sonnet/Anthropic baseline is byte-for-byte unchanged.
- Supersedes the routing structure of ADR 0001; the role→model *intent* there
  still holds.

## Smoke test (2026-07-20)

`uv run eval-agent --limit 3 --skip-judge` on the GLM arm: 3/3 questions
complete end-to-end, hallucination 0.000, no crashes — confirms GLM
synthesis/critic is viable through the full graph. The metric *values*
(numeric_fidelity ~0.58, coverage ~0.33) are **not** a quality verdict: n=3,
judge skipped, and noisy across runs. The go/no-go on GLM vs Sonnet needs the
full 30-question `eval-agent` (judge on) against the Sonnet baseline, with the
model frozen before `--write-baseline`.
