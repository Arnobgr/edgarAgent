# ADR 0021 — Retraction is a set operation; concept terms match both ways; a year-scoped balance means year-end

**Date:** 2026-07-31 · **Phase:** 3 (post-gate remediation) · **Status:** accepted

## Context

Three defects found in the accepted baseline's own artifacts
(`docs/findings/2026-07-30-recall-decomposed-and-collateral-excision.md`),
all in the path between a rejected claim and the answer the reader gets.

**1. Excision deleted the wrong sentences.** `retract` iterated unsupported
claims and called `excise_claim` per claim, each re-deriving its target by
best token overlap **over a draft it had already mutated**. One draft's
claims overlap heavily in token space — g-011's were all of the form "Data
Center revenue was $X billion, up Y% year-on-year" — so a claim's best match
was frequently the sentence stating a *different* claim. One removed sentence
carried 16 claims above the 0.5 threshold; another 14. Across the 11 runs
that ran a retraction pass, **26 of 204 answer sentences (12.7%) were
deleted**, and g-011 alone lost 15 of 35.

Two consequences, opposite in sign:

- Four claims were logged "could not locate", kept `unsupported`, and
  reported to the reader as text that *may still appear above*. Their text
  was gone in all four cases. `hallucination_rate` 0.0145 was entirely
  artifact; surviving hallucinated text in the accepted run is 0 of 275.
- Three **`grounded`** claims lost their home sentence to another claim's
  excision and stayed in the grounding table as delivered, verified claims.
  A reader got an answer with verified content silently removed and a table
  citing sources for prose that is not there.

**2. A concept term longer than the filed label could never match.**
`query_facts` built `%<term>%` in all three of its match directions, so
`'%ProductsRevenue%'` against a filed label of `Products` is unsatisfiable by
construction. ADR 0018 bridged a *space* difference (`NetSales` → "Net
sales"); it cannot bridge a suffix the label does not carry. g-013
`claim-4-6` is arithmetically correct with both operands filed
((316,199 − 298,085)/316,199 = 5.73%) and was ruled unsupported on this alone.

**3. A fiscal year has four balance dates and the verifier accepted all of
them.** `_period_admissible` admits any same-fiscal-year instant, deliberate
under ADR 0016 while instants carried no date at all. ADR 0020 gave them
dates; nothing consulted them. The 16 `period` perturbations in
`eval/verifier_claims.jsonl` are all *flow* concepts with shifted fiscal
years, so the class was assumed safe rather than measured. Eight new claims
stating a quarter-end balance as the year-end figure were accepted **8 of
8**, taking verifier precision from 1.000 to 0.889 (numeric 0.873).

## Decision

**1. Excision is computed once, over the un-mutated draft, as a global
assignment.** Each unsupported claim takes at most one sentence and each
sentence serves at most one claim; ties break toward the strongest match,
then the earliest claim, then the earliest sentence — the old
`score >= best_score` let whichever sentence came last win a tie, so the
outcome depended on iteration order.

**2. Whether a claim left the answer is decided by comparing the draft before
and after that pass**, not by whether its own excision call reported success.
A claim whose sentence another claim's excision removed has genuinely left
the answer and counts as `retracted`; a claim whose text survives stays
`unsupported`. The 2026-07-26 rule is preserved exactly — surviving
unsupported text must keep counting against `hallucination_rate` — and only
its detection is repaired.

**3. A `grounded`/`partial` claim whose sentence is gone is dropped from the
grounding list and logged.** For a system whose premise is "verified against
a source or explicitly retracted", citing a source for prose the user never
saw is the precise failure it exists to prevent.

*No new `ClaimStatus` member.* It would ripple through `build_run_result`,
the `RunResult` contract and every metric denominator, for a state affecting
3 claims in 275 — below the noise floor of everything it would touch — and
ADR 0023 makes the state extinct.

*Detect and correct in the graph; assert in the test.* A hard assertion in
the graph would have killed the accepted run over those three claims. The
invariant is stated absolutely in `tests/test_graph.py`: every surviving
`grounded`/`partial` claim's sentence is still present, every `retracted`
claim's is gone.

**4. Concept terms also match field-inside-term**, over the space-stripped
forms, behind a minimum label length so generic labels ("Total", "Other",
"Net", "Cost") cannot match every compound term an analyst could write.

**5. A mapping with a fiscal year and no period reaches only the year's
closing balance.** That is what the mapper emits for an "as of" figure.
Durations are untouched, and a claim naming its quarter still reaches that
quarter through the stated-period query.

## Consequences

- **Two gate metrics move for bookkeeping reasons alone**, with no change in
  system behaviour: the four collateral claims re-classify to `retracted`,
  so `hallucination_rate` → 0.000 and `grounding_rate` → ~0.948.
  **`0.000` is precisely the value CLAUDE.md flags as an artifact of the
  pre-2026-07-26 retraction bug**, so a future reader must be able to tell
  the two apart. The distinguishing evidence is the pre/post-retract diff and
  the invariant test; a 0.000 with no invariant asserted is suspect.
- The measured blast radius of the both-directions match, per term, is
  correct labels only — `ProductsRevenue` 0 → 113 rows (`Products`,
  `Revenue`), `ServicesRevenue` 0 → 113 (`Services`, `Revenue`),
  `TotalRevenues` 35 → 140 (`Revenue`, `Revenues`),
  `ResearchAndDevelopmentExpense` 152 → 218 (`Research and development`),
  `NetIncomeLoss` 454 → 515 (`Net income`). `NetSales`, `GrossProfit`,
  `OperatingExpenses` and `TotalAssets` are unchanged.
- **Verifier P/R, measured at each step** over the 128 labeled claims
  (112 existing + A5's 16):

  | state | precision (all) | numeric | recall | balance_date correct |
  |---|---|---|---|---|
  | before A5 (class unmeasured) | 1.000 | 1.000 | 1.000 | — |
  | A5's claims land | 0.889 | 0.873 | 1.000 | 0/8 |
  | year-end rule, period `None` only | 0.955 | 0.948 | 1.000 | 5/8 |
  | **guard widened to `None` or `FY`** | **1.000** | **1.000** | **1.000** | **8/8** |

  The intermediate 0.955 is instructive: gating on period `None` alone left 3
  accepted, because the mapper emits `FY` for "at the end of FY 2024" and the
  relaxed instant pass after a stated period re-admits every balance of the
  year. **Recall never moved** — all 64 real claims stay accepted at every
  step, so closing the class cost nothing on the labeled set.
- **Cost of the year-end rule, stated plainly:** a claim about a *quarterly*
  balance that does not name its quarter no longer grounds against the store
  and falls through to the cited table-chunk match. No labeled claim has that
  shape, so the cost is bounded by argument, not measured. The ratio and
  difference paths keep admitting any same-year instant — their operand pairs
  are gated on matching accession and period, and no claim of that shape is
  labeled either, so that class stays **unmeasured rather than assumed safe**.
- Token-overlap excision survives here only as ADR 0023's fallback.

## Rejected

**"Refuse to delete a sentence that hosts a grounded claim."** It sounds
right and fails the gate: the unsupported claim then survives in the prose
and must stay `unsupported`, pushing `hallucination_rate` above its 0.02
target. Stage B changes the accounting; ADR 0023 changes what gets deleted.
