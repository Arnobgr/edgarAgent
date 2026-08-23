# ADR 0044 — A cited fact grounds its own difference

**Date:** 2026-08-23 · **Phase:** 6 · **Status:** accepted
· **Relates to:** ADR 0019 (the difference path this extends), ADR 0022
(injected fact chunks and why they are `xbrl` and not `table`), ADR 0018
(recomputation as a grounding method), ADR 0012 (the grounding rule)

## Context

Run `11f727f75ca6` of the accepted baseline answers "the trend in Apple's R&D
spending over the last three fiscal years". It states the three levels, grounds
all three, and then states the two deltas between them:

> The year-over-year increase from FY 2023 to FY 2024 was approximately $1,455 million.
> The year-over-year increase from FY 2024 to FY 2025 was approximately $3,180 million.

Both are exactly right — 31,370 − 29,915 and 34,550 − 31,370 — and both were
retracted. The reported cause was:

> figure(s) $1,455 million not found in the XBRL facts store — searched XBRL
> concepts: ChangeInAmount, IncreaseDecrease; no table chunks cited.

Three things about that are worth stating, because each one on its own would
suggest a different fix.

**It is not a missing capability.** `_match_difference` (ADR 0019) exists to
recompute exactly this shape, and the store holds both operands under
`us-gaap:ResearchAndDevelopmentExpense` with `fiscal_period` `FY`.

**It is not a store gap either, in the sense the message implies.** XBRL really
does not carry this figure: no AAPL fact equals ±1,455,000,000 or
±3,180,000,000. Companies file levels per period. The `IncreaseDecreaseIn*`
elements that do exist are cash-flow and roll-forward line items in their own
right — `IncreaseDecreaseInInventories`, `GoodwillPeriodIncreaseDecrease` —
never a general "change in X". A delta must be derived, which is what ADR 0019
already concluded.

**The mapping is where it broke, and the mapping could not have succeeded.**
`_map_claim` sends the model the system prompt and `f"Claim: {claim_text}"` —
nothing else. The claim text never says *what* increased. The prompt does ask
for "the changing concept's terms", but that concept is not in the input, so
the model answered with names that do not exist. Two structurally identical
claims got two different invented mappings (`ChangeInAmount`,
`IncreaseFromFY2024ToFY2025`), which is the signature of a guess rather than a
rule.

Meanwhile both operands were sitting in the claim's own citations, as injected
fact chunks carrying their concept and period end.

The reconciler already refuses to trust the model for the *ticker* — it reads
that off the cited chunks (`tickers = sorted({c.ticker for c in chunks})`). It
did not do the same for the concept.

## Decision

**When a change amount has no fact match, recompute it from the facts the claim
cited.** `_cited_facts` resolves each `chunk_type == "xbrl"` evidence chunk back
to its store row by `(ticker, concept, period_end, accession)`;
`_match_cited_difference` subtracts every admissible pair and grounds at the
claim's stated precision, reusing `_difference_span`. It runs after
`_match_fact` and before the table-chunk fallback.

Pairs are gated on `(ticker, concept, label, unit)` and must differ in
`period_end` — the same "like subtracts from like" rule `_match_difference`
uses, plus the unit.

## Why this does not loosen the grounding rule

The concept only *selects*. The values and the subtraction still come from the
facts store, so a figure that is not the difference of two filed values still
fails — `test_a_wrong_delta_over_the_same_cited_facts_stays_unsupported`.

It is in fact a tightening. Before this, the reconciler could ground a claim
against a fact the claim never cited: the model proposed any concept it liked
and the store was searched globally by value. Now the delta grounds against
what the claim actually pointed at, and a delta over facts the claim did not
cite stays unsupported
(`test_a_delta_over_facts_the_claim_did_not_cite_stays_unsupported`).

This is also not the circularity that `chunk_type: "xbrl"` exists to prevent
(ADR 0022). That rule stops the *table cell matcher* from string-matching
injected text which restates the store's own value — text matching text. Here
no text is matched; a row identifier is read, and the arithmetic is done on
stored values.

## What this deliberately does not do

**Percent changes stay with `_match_ratio`.** The same defect shape plausibly
exists for "R&D grew 4.9%", but it was not observed and is not assumed. Adding
it would be speculation with no reading behind it.

**Level claims are untouched.** A claim citing one XBRL chunk and restating its
value already grounds through the mapping; nothing measured says otherwise.

**The subject-less claim is not fixed, only worked around.** The synthesis
prompt requires each claim to be "one self-contained sentence… it must stand on
its own" (`context/__init__.py`), and these two are not. Nothing enforces it.
Fixing that means changing a prompt on the answer path, which invalidates the
baseline and costs a full run — a separate decision, deliberately not bundled
here so that a later eval can attribute the movement.

## Consequences

Two claims of 212 in the 30-run baseline were retracted for this cause (2 of 12
retractions). Both ground under the change, replayed against the real corpus
with the mapping the model actually produced. The effect on the gate can only be
`unsupported → grounded`, so `hallucination_rate` — the one ratcheted metric
(ADR 0043) — cannot rise from it.

The eval that would measure this has not been run; `make check` is green (292
tests) and the failure is reproduced and fixed under test.
