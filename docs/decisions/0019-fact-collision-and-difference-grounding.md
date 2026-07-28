# ADR 0019 — The store was silently deleting disaggregation facts; differences ground by recomputation

**Date:** 2026-07-28 · **Phase:** 3 · **Status:** accepted

## Context

The second re-baseline attempt (2026-07-28, `agent-20260728-192552.json`) ran
30/30 with `hallucination_rate` 0.015 but missed `grounding_rate` (0.891 vs
0.90) and `numeric_fidelity` (0.759 vs 0.85). Classifying every failure from
checkpoints found **zero fabrications** — and one root cause under most of
them:

**The facts table's uniqueness key deleted most dimensioned facts.** The key
was `(accession_no, concept, unit, period_start, period_end, dimensions)`
with `INSERT OR IGNORE`, while this edgartools version collapses dimensions
to a `{"dimensioned": "true"}` flag (the accepted "dimensions collapsed to a
flag" deferral). Every axis member of one concept and period — Products,
Services, iPhone, Mac, iPad, Wearables, NVDA's Data Center — collided on the
key, and only the first row survived. The tell: the store held "Net sales"
and "Products" for *every* Apple period but not a single "Services" row.
NVDA's Data Center revenue — treated as "never filed" throughout the Phase 3
diagnosis — was filed all along and being discarded at ingest. The deferral
was accepted believing it only degraded dimension *metadata*; it was
deleting ground truth.

The residue after the store fix was two figure classes the reconciler could
not verify: change amounts ("declined by $2,057 million") and Q4 figures
("Q4 FY2025 revenue was $102.5 billion" — Q4 is never filed; analysts derive
it as FY minus YTD9), plus the same two classes scored as misses by
`numeric_fidelity` along with bare-`$` table cells under an "(in millions)"
header ("$23,329") that the metric read six orders of magnitude small — the
table-form sibling of the ADR 0017 suffix-letter gap.

## Decision

1. **`label` joins the facts UNIQUE key.** With dimensions collapsed, the
   label is what distinguishes axis members; same label and all else equal
   is a true duplicate. Facts re-extracted for the whole corpus (SEC EDGAR
   network only, no paid API): 28,550 facts, disaggregation intact.
2. **Difference form in the reconciler** (`_match_difference`): a non-percent
   figure failing direct lookup grounds when it equals `|value_Y −
   value_{Y−1}|` (same concept, same period, adjacent years) or `|FY −
   YTD9|` (same concept, same year), at the claim's stated precision. The
   pair gate requires identical ticker, concept **and label** — with
   dimensions collapsed, label equality is what stops a headline value
   pairing with a segment value.
3. **`numeric_fidelity` applies the answer's context scale**: figures
   without their own scale word also try the "(in millions)"-marker scale,
   tolerance scaled alongside — the same rule the reconciler has always
   applied to source chunks. This tightens rather than loosens: a scaled
   figure's window is ±half its last printed digit at true magnitude.

## Consequences

- Re-scored on the refused run's own artifacts: `numeric_fidelity` 0.759 →
  **0.938**; 10 of 24 numeric grounding failures flip → projected
  `grounding_rate` ~0.93. Verifier P/R re-measured after both the store and
  reconciler changes: **1.000/1.000 on all 112 labeled claims**.
- The store fix retroactively re-frames earlier diagnosis: several figures
  written off as "correct but with no XBRL counterpart" (ADR 0015/0017
  residuals, g-011's platform breakdowns) were extraction losses, not filing
  gaps. The honest ceiling of both metrics was being measured against a
  corrupted store.
- The "dimensions collapsed to a flag" deferral is closed as a bug, not
  re-litigated as a feature request. Recovering *real* axis metadata (not
  just survival-by-label) remains open and Phase 5-shaped.
- False-accept surface of the difference form: bounded by printed-precision
  tolerance, concept+label equality, and year adjacency; the labeled-set
  P/R run remains the standing tripwire.
