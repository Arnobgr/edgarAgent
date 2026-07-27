# ADR 0016 — Reconciler: financial-figure obligations, period-free retry, ratio recomputation

**Date:** 2026-07-27 · **Phase:** 3 · **Status:** accepted

## Context

The grounding_rate diagnosis (`plans/phase-3-metric-diagnosis.md`) decomposed
the 53 non-grounded claims of the 2026-07-27 baseline and found 47 were
`partial` verdicts from numeric reconciliation, most of them mechanical
defects rather than bad claims:

- **18** failed only because `parse_numbers` over the claim text produced
  non-amounts the reconciler then demanded reconcile against XBRL: date
  fragments ("September 27, 2025" → 27), quarter ordinals ("Q1" → 1),
  durations ("more than 12 months" → 12), form names ("10-K" → 10). Every
  dollar figure in these claims had matched.
- **~7** were six/nine-month figures whose facts exist in the store under
  `YTD6`/`YTD9` — labels the mapping schema's `fiscal_period` description
  ("'FY' or 'Q1'..'Q4'") made unreachable.
- **3** were balance-sheet claims hitting the instant-fact contract: instants
  are stored with `fiscal_period = NULL` (ADR 0015), the mapper says "FY",
  and `query_facts` matches exactly.
- **14** were ratio percentages (margins, % of revenue) — derived figures
  that are never filed facts, though both operands are in the store.

Each bogus `partial` also fires a verify→research loop-back, burning budget
every run.

## Decision

Four changes to `XbrlNumericReconciler`, none touching retrieval, synthesis
or model selection:

1. **Only financial figures are reconciliation obligations** — currency,
   explicit scale word, or percent. Other parsed numbers are context. A
   numeric claim with *no* financial figures falls back to
   `InterimNumericReconciler` over the cited chunks: its numbers are prose
   details ("12 months"), verified against the cited text like any quote.
2. **Period-free retry, never year-free.** The lookup tries the mapped
   `fiscal_period` first, then retries without a period filter. Fiscal year,
   ticker scope, concept terms and value-at-stated-precision still gate the
   relaxed match. The fiscal *year* is never dropped: the right value under
   the wrong year staying unsupported is the reconciler's core guarantee
   (`test_period_shift_is_caught`).
3. **YTD vocabulary.** `FactCandidate.fiscal_period` now names
   `YTD6`/`YTD9`, and the mapping prompt says six/nine-month figures use
   them, balance-sheet "as of" figures map to a null period, and fiscal
   years follow the company's fiscal calendar.
4. **Ratio recomputation for percent figures.** The mapping may name
   `numerator_terms`/`denominator_terms`; the reconciler queries both
   operand sets, pairs facts sharing (ticker, accession, fiscal year,
   period), and grounds the percent when a pair's ratio matches at the
   claim's printed precision. The supporting span names both facts, so the
   evidence trail stays inspectable.

## Consequences

- Expected grounding on the baseline answers: the 18 noise-only claims
  ground deterministically; YTD/instant/ratio classes additionally need the
  mapper to emit the right fields, which the schema and prompt now permit —
  ceiling ~0.94 vs the 0.90 target if it does.
- **Verifier behavior changed, so the measured ~0.98 P/R no longer applies.**
  Re-run the verifier eval on the labeled claims before trusting new
  grounding numbers (the numeric arm needs the mapper model — paid, small).
- Coincidental-pair risk in ratio recomputation is bounded the same way
  direct matches are: printed-precision tolerance, plus same-accession/
  period pairing and mapper-named operand concepts. Loosening any of these
  gates re-opens the chance-floor failure mode documented in ADR 0015.
- A residual known miss: instants whose calendar date falls in a later
  fiscal year than the claim's stated year (NVDA "as of April 26, 2026" is
  fiscal 2027). Accepted — fixing it would mean relaxing the fiscal-year
  gate, which protects against a worse error class.
