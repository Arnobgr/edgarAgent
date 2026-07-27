# ADR 0015 — Re-scope numeric_fidelity to financial figures; eval-harness integrity fixes

**Date:** 2026-07-27 · **Phase:** 3 · **Status:** accepted

## Context

The 2026-07-27 exit-gate baseline (n=29) read `numeric_fidelity` 0.566 against
a ≥ 0.95 target, barely above the measured ~47% chance floor. Task 1 of
`plans/phase-3-metric-diagnosis.md` decomposed every number the metric scored
in that run, recovered from the LangGraph checkpoints:

- **1,352 numbers scored; only 83 (6%) were financial figures.** 1,085 (80%)
  were digit runs inside bracketed citation ids
  (`[0001628280-25-045968:table-0011]` → "0001628280", "25", "0011"), 48 sat
  in the appended "### Retracted claims" section, and 136 were dates, list
  ordinals, form names ("10-K" → 10) and product names ("H200" → 200). The
  non-figures matched the store at ~50% — exactly the chance floor — which is
  why the metric sat near 0.5 regardless of model.
- **The 83 genuine figures matched at 0.88**, and the match set contained the
  *right* fact (same company, concept, period) for ~90% of passes.
- **All 10 genuine failures were legitimate**: 4 derived arithmetic the answer
  itself labels as computed ("total net sales minus Services", "1.46 times"),
  6 correct figures that are not XBRL facts (segment gross profit, share
  repurchase prices, TSR graph values). Zero fabricated numbers, zero scale
  errors.

The same diagnosis surfaced four harness-integrity defects, all instances of
the retraction-bug pattern (a metric or gate routing around the failure it
exists to catch):

1. `expected_fact_coverage` dropped unresolvable expected facts from the
   denominator (5 of 15 labels in the baseline run). Root cause of the
   unresolvable labels: instant (balance-sheet) facts are stored with
   `fiscal_period = NULL` — a fiscal year holds several balance dates
   (year-end plus comparatives) and the ingest records no period for them —
   while the labels said `fiscal_period: "FY"`, which `query_facts` matches
   exactly. Unsatisfiable by construction.
2. A question whose run raised was logged and dropped (g-010), silently
   shrinking every metric's denominator; its checkpoint shows a completed,
   fully verified answer, so the failure was in the post-graph tail, not the
   graph. `--write-baseline` would then have recorded the 29-question failing
   run as a clean accepted baseline (it did; reverted by hand).
3. Citation-judge API failures were scored as `supported=False` — harness
   errors counted as citation errors.
4. Citation judgments were not persisted and their claim ids (`claim-1-1`)
   repeat across questions, so diagnosing `citation_accuracy` 0.728 requires
   a paid re-judge.

## Decision

**Scope `numeric_fidelity` to figures the answer presents as amounts** — a
currency symbol or an explicit scale word — parsed after stripping bracketed
citation markup and truncating at `RETRACTED_HEADER` (now part of the answer
contract in `fie.state`). Tolerance and matching are unchanged; percent
figures stay excluded. On the 2026-07-27 answers the re-scoped metric reads
**0.878**.

Harness fixes, batched before the next paid run:

- `expected_fact_coverage`: every expected XBRL fact of an answerable question
  stays in the denominator; unresolvable facts and missing results score as
  misses. Matching accepts any resolved candidate value, because instant
  facts cannot be disambiguated to a single balance date. The five instant
  labels drop their unsatisfiable `fiscal_period`.
- `eval-agent` fails loudly (non-zero exit, red banner) when
  `n_completed < n_questions`.
- `--write-baseline` refuses to record a run that misses the §13 targets or
  dropped questions unless `--force` is given, and records
  `n_questions`/`n_completed` in `baselines.json`.
- Citation judgments are persisted per pair in the report under run-id
  qualified claim ids; judge failures are excluded and counted separately.
- The per-run `IngestStore` (and the verifier-eval reconciler) is closed when
  the run ends instead of leaking a SQLite connection per question.

## Consequences

- `numeric_fidelity` becomes a measurement of the thing it names. Its honest
  residual: derived figures and correct-but-not-filed narrative figures still
  score as misses, so **a perfect system does not reach 1.0 under this
  definition** — the observed ceiling on real answers is roughly 0.9. The
  ≥ 0.95 §13 target predates this understanding and needs re-setting (or the
  fact-reference rendering alternative in the diagnosis plan) — that is a
  gate decision, recorded there, not here.
- `expected_fact_coverage` numbers drop discontinuously versus older reports
  (the denominator grew); pre-2026-07-27 values are not comparable.
- The store still cannot name which balance date an instant fact carries
  (`period_end` is NULL for instants). Fixing that in ingest would let
  coverage and reconciliation pin year-end balances exactly; deferred until a
  need earns it.
