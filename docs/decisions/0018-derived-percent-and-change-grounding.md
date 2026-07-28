# ADR 0018 — Derived percents ground by recomputation; mapper vocabulary reaches spaced labels

**Date:** 2026-07-28 · **Phase:** 3 · **Status:** accepted

## Context

The 2026-07-27 re-baseline left one gate metric failing: `grounding_rate`
0.893 (216/242 non-retracted claims; the report renders the reduced fraction
108/121) against a 0.90 target — 26 non-grounded claims, needing two more
grounded to pass. Classifying all 26 from the checkpointed states (free, no
re-run) and probing the store and the live mapper per class:

- **9 were year-over-year change percents** ("up 41% from $16,405M",
  "declined approximately 5.7%") whose operands are filed facts. Two
  structural blocks: `_match_ratio` computed only `num/den` — a change is
  `(a−b)/b` — and its coherence gate required the operand pair to share a
  fiscal year, which a YoY comparison never does.
- **5 were plain ratios** ("R&D … representing 9% of net sales"). The mapper
  emits camel-case vocabulary (`NetSales`); the store label is filed with
  spaces ("Net sales") and the concept is
  `RevenueFromContractWithCustomerExcludingAssessedTax`, so the
  `LIKE '%NetSales%'` term match reached neither and the ADR 0016 ratio path
  found zero denominators. The path itself was verified correct with a
  handcrafted mapping.
- **2 were day-of-month fragments** in the cited-text path ("As of September
  27, 2025 … 14,773,260 shares" demanded a bare `27` in the chunk) — the
  defect class ADR 0016 removed from the XBRL path, still live in the
  `InterimNumericReconciler` fallback.
- **The rest are correct rulings**: qualitative partials naming real
  claim/source discrepancies, segment growth with no filed counterpart
  (g-011's 133%), and g-012's 19.0% automotive margin, for which no coherent
  operand pair in the store yields 19.0% at stated precision (automotive
  revenues 57,272M → 19.91%; segment revenues 64,958M → 17.55%).

## Decision

Four changes, all reconciler-side (retrieval, synthesis prompts and models
untouched, per the Task 5 scope guard):

1. **Change form in `_match_ratio`.** After same-period ratio pairs, try
   `|num|/|den| − 1` with numerators at the mapping's fiscal year and
   denominators at year − 1. The pair gate swaps accession equality for
   **concept equality**: the comparative may sit in a different filing, but
   like must compare to like — revenue growth must not ground against last
   year's gross profit hitting the ratio by luck. The mapping prompt now asks
   for the changing concept as both operand term lists.
2. **Derived-percent pass in `reconcile`.** A percent that failed lookup but
   is arithmetically consistent — change `|a−b|/|b|` or share `|a/b|`, at its
   stated precision — with figures *already reconciled in the same claim*
   grounds with a span naming the derivation. Operands must themselves have
   matched (store or table fallback), so the percent adds no unverified
   information. Percents never serve as operands.
3. **Space-stripped label matching in `query_facts`.** Terms also compare
   against `REPLACE(label, ' ', '')`, bridging mapper camel-case to filed
   labels. Purely additive to the match set.
4. **Day-of-month fragments are not obligations in the cited-text path.** A
   bare integer 1–31 directly preceded by a month name is skipped, unless the
   claim contains nothing else.

## Consequences

- Replaying the 26 failures through the fixed reconciler (real store, real
  mapper): **11 of 20 numeric failures ground**, projecting ~227/242 ≈ 0.94
  on the same claim population. The correct rulings stay put — g-011's 133%
  and g-013's 14.2% remain unsupported.
- Verifier P/R re-measured after the change (ADR 0016's standing rule):
  **1.000/1.000 on all 112 labeled claims**, unchanged.
- False-accept surface: a wrong percent could coincide with an unrelated
  operand pair's arithmetic within its printed precision. The precision gate
  (±0.5 at zero decimals, ±0.05 at one) keeps this small, and the labeled-set
  P/R run is the standing tripwire. The concept-equality gate on the change
  form deliberately forgoes some reach (mixed-concept changes) for safety.
- `test_period_shift_is_caught` and the vc-period-019 admissibility rules are
  preserved — the change form never drops the fiscal year and reuses
  `_period_admissible` on both sides.
