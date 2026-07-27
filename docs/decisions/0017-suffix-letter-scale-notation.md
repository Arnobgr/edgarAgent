# ADR 0017 — `parse_numbers` reads suffix-letter scales ("$298,085M")

**Date:** 2026-07-27 · **Phase:** 3 · **Status:** accepted

## Context

The 30-question re-baseline (`eval/reports/agent-20260727-224458.json`,
n=30/30) cleared the run-integrity bar the previous attempt failed — no
dropped questions, `hallucination_rate` 0.017 against a ≤ 0.02 target, verifier
P/R 1.000 — but `numeric_fidelity` read **0.654 (53/81 figures)** against the
0.85 target set in ADR 0015, and against a projection of ~0.88 derived from
re-scoring the previous run's answers.

Classifying all 28 misses from the checkpointed answers (free; no re-run):

- **18 were the same defect.** `_NUM_RE` accepted the spelled-out scale word
  ("$391 billion" → 3.91e11) but not the suffix-letter form, so "$298,085M"
  parsed as **298,085** — six orders of magnitude below the stored fact — and
  "$71.6B" as 71.6. Every such figure misses by construction. Two answers
  (g-013, g-009) wrote most of their figures that way, which is why the metric
  moved so far on answers of otherwise normal quality.
- **10 were legitimate** and match ADR 0015's documented residual: seven AAPL
  Products/Services comparatives ($85,200M, $78,129M, $68,425M — correct
  figures with no counterpart in the ingested XBRL) and three per-share
  repurchase prices. **Zero fabricated figures, zero wrong-fact matches.**

This is the "bucket B — right fact, wrong magnitude" case in
`plans/phase-3-metric-diagnosis.md`, whose decision rule is to fix scale
handling. Retrieval and synthesis are not implicated and were not touched.

`parse_numbers` is shared with the runtime reconciler, so the same gap makes a
claim written "$298,085M" unreconcilable against the facts store. Its effect
there is much smaller — 2 of the 13 non-grounded claims in the final answers
use the notation — because claim text more often spells the scale out. Those
13 break down as 8 numeric/`partial`, 3 qualitative/`partial` and 2
numeric/`unsupported` (the two that constitute `hallucination_rate`).

## Decision

Teach `_NUM_RE` the suffix forms `M`, `MM`, `B`, `bn` (case-insensitive),
mapping to 1e6/1e6/1e9/1e9. Nothing else about parsing, tolerance or matching
changes.

Letter suffixes require a **trailing word boundary**; the spelled-out words
keep their original looser match. Without the boundary, `M` would match the
first letter of any following capitalised word and "$5 Model Y" would parse as
$5 million. Regression tests cover that case plus "$5 basis points",
"100 MWh" and "10-K".

`K` is deliberately excluded: "10-K", "S-K" and "401(k)" are common in this
corpus and thousands-suffix notation is not.

## Consequences

- Re-scoring the same 30 answers with the fixed parser: `numeric_fidelity`
  **0.654 → 0.877**, above the 0.85 target and matching the ~0.88 projection.
  The residual 10 misses are the honest ceiling ADR 0015 describes.
- The verifier can now reconcile suffix-notation claims, so `grounding_rate`
  should improve slightly. It read 0.893 (108/121) — 0.90 needs one more
  grounded claim — but only 2 non-grounded claims carry the notation, and both
  are ratio claims whose *percent* is the primary failure, so this fix alone
  is not guaranteed to clear the target.
- **A metric moved a long way on a notation change, not a quality change.**
  Both `numeric_fidelity` and the reconciler compare rendered prose against
  stored values, so they stay sensitive to how the synthesis model chooses to
  write figures. That is an argument for the fact-reference rendering
  alternative (ADR 0015, deferred to Phase 5), not against the metric.
- This changes verifier behavior, so verifier P/R must be re-measured on the
  labeled claims before the numbers are trusted — the plan's standing rule for
  reconciler changes (ADR 0016).
- `expected_fact_coverage` (informational, 0.250 = 7/28) is **not** explained
  by this defect: re-scoring with scales normalized leaves it at 7/28
  unchanged. Its cause is unrelated and undiagnosed.
