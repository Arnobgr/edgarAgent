# ADR 0004 — Verdict taxonomy

**Date:** 2026-07-18 · **Phase:** 1 · **Status:** accepted

## Decision

Every claim decomposed from a draft answer gets exactly one verdict per
verify pass, from a three-value taxonomy:

| Verdict | Meaning | Effect |
|---|---|---|
| `grounded` | The cited source fully supports the claim | Claim survives |
| `partial` | Supported only with material caveats/approximations | Claim survives, caveat surfaced in the grounding table |
| `unsupported` | The cited source does not support (or contradicts) the claim | Drives the verify→research loop-back; retracted at iteration exhaustion |

Claim *status* is a superset of the verdict labels: `pending` (pre-verify)
and `retracted` (excised after iteration exhaustion) in addition to the
three verdict values. A `Verdict` also records the `method` that produced
it, fixed by claim type:

- `numeric` → `numeric_reconciliation` (deterministic tool, no LLM verdict),
- `qualitative` → `semantic` (cheap-model entailment judge),
- `forward_looking` → `attribution` (cheap-model attribution check; also
  used for the auto-`unsupported` verdict on claims with no citation).

Each verdict carries `supporting_span` (the exact quoted source text when
grounded/partial) and `feedback` (why it failed — this text is carried back
to research/synthesis on the loop-back).

## Rationale

- Three verdicts are the minimum that distinguish "keep", "keep with a
  flag", and "reject": a binary verdict either drops defensible-but-
  imprecise claims or lets fabrications through.
- `partial` is accepted (not looped back) because the loop budget is scarce
  and a partially supported claim is visible — the grounding table shows the
  caveat, which is the honest presentation (blueprint §4: never silently
  drop or silently keep).
- Retraction is a *status*, not a verdict: it is decided by the iteration
  cap in the conditional edge and executed as a deterministic code step
  (sentence excision + a listed "Retracted claims" section), never as an
  LLM rewrite — a claim reaches the output only by re-passing grounding,
  never on the synthesizer's compliance.

## Revisit

Phase 3 labels a claim set and measures per-type verifier precision/recall
(target ≥0.90 / ≥0.85, overview §3.5); types below threshold escalate to
the frontier model. If `partial` turns out to hide real errors on the gold
set, it becomes loop-back-eligible.
