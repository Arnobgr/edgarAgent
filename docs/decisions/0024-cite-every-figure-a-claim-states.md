# ADR 0024 — Cite every figure a claim states

**Date:** 2026-07-31 · **Phase:** 3 (post-gate remediation) · **Status:** accepted ·
**Extends:** ADR 0022 (span rendering), ADR 0018 (derived-percent grounding)

## Context

`citation_accuracy` was the one metric the remediation made worse: 0.623 →
0.370 on the same ten questions, recovered to 0.556 by the span-rendering fix
(ADR 0022) and still short. The residual was attributed to claims asserting
document locations, and closing it was called a synthesis-prompt matter and
therefore out of bounds. That attribution was incomplete.

**The drop is a change in the claims, not in the citations.** Both runs render
spans in the same XBRL form — 47/47 before, 23/23 after. What changed is what
the claims assert:

| numeric claims | baseline | post-remediation |
|---|---|---|
| plain (entity, metric, period, value) | 28 of 47, scoring **0.857** | **0 of 23** |
| carrying an extra assertion | 19 of 47, scoring 0.421 | **23 of 23** |
| median claim length | 87 chars | 140 chars |

The per-class rates barely moved (document location 0.000 → 0.000, comparison
0.125 → 0.143). The population moved: the high-scoring plain claims vanished
entirely. Three causes, all traceable to this remediation:

1. **ADR 0023 added "This is what the verifier checks, so it must stand on its
   own"** to the synthesis prompt. A claim that stands on its own absorbs its
   context — the statement it came from, the exact date, the prior year.
2. **ADR 0022's fact injection** puts XBRL rows in the evidence, so the model
   writes about them ("The XBRL-tagged NetIncomeLoss fact reports…").
3. **ADR 0020 gave every fact a date**, so claims now state exact period ends:
   26% of numeric claims before, 78% after.

**Within that population, one real defect.** `reconcile()` verifies *every*
figure in a claim — `verdict="grounded"` requires `not missing` — and builds a
span for each in `matched_spans`. It then returned `matched_spans[0]` and
discarded the rest. A claim stating a level, a change and a percent had all
three reconciled and cited evidence for the level alone. The citation judge,
told to require support for every part of a claim, correctly refused it:
**1 of 7 comparison claims passed while all 7 were fully verified.** Both
reconcilers had the line; the interim table-cell one has it too.

This is the same shape of defect as ADR 0022's `_amount` formatting — the
grounding was exact, the evidence shown for it was not — and the same shape of
lesson as ADR 0015: a metric reading something other than what it names.

## Decision

**Join every matched span, deduplicated, in the order the claim states the
figures.** `_join_spans()` replaces both `matched_spans[0]` sites. Dedup
matters because two figures in one claim can resolve to one fact.

Not chosen: suppressing multi-figure claims at synthesis, or relaxing the
judge. The first destroys information the analyst asked for; the second is
tuning the instrument to the result, which this project has already paid for
once (ADR 0015).

## Consequences

Re-judged on the subset run's own claims, live reconciler, same judge, same
prompt — only the span differs:

| | citation_accuracy |
|---|---|
| baseline, same 10 questions | 0.623 |
| post-remediation, as measured | 0.370 |
| + span rendering (ADR 0022) | 0.556 |
| **+ every figure cited** | **0.741** |

Comparison claims go **1/7 → 6/7**; exact-date 11/18 → 13/18. Six claims
recovered and one flipped the other way **on a byte-identical span** — the
judge is non-deterministic at roughly 1 in 27, which is the noise floor for
reading any single `citation_accuracy` measurement.

**`citation_accuracy` does not gate.** It is written into `baselines.json` but
is absent from `TARGETS`, so `--write-baseline` never refuses a run over it.
The earlier claim that it would block Stage E was wrong.

**The residual (6 of 23) is genuinely out of reach of the reconciler**, and
this part of the earlier reading holds. Three kinds, all correct figures:

- claims naming a document location a single XBRL row cannot prove — "the
  segment operating performance table in MD&A", "the Consolidated Statements
  of Cash Flows";
- claims citing the 10-K while naming the 10-Q, or naming "cash, cash
  equivalents **and restricted cash**" against
  `CashAndCashEquivalentsAtCarryingValue` — a real concept mismatch the judge
  is right to catch;
- narrative qualifiers ("Tesla reported record revenue in Q3 2025").

Closing those means constraining what synthesis may assert, measured against a
gate metric. It stays an open decision, not an oversight.

## Honest limits

- Measured on 10 of 30 questions, 27 judged pairs. The full-set number will
  differ, and one judge flip moves it by 0.037.
- `cli.py` truncates the grounding table's span at `MAX_SPAN_CHARS = 100`,
  which already cut a single XBRL span short and now hides the added ones. The
  JSON output and the judge see the whole span. Untouched here as pre-existing.
