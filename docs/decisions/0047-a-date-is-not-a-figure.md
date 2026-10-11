# ADR 0047 — A date is not a figure: date-only claims are judged by entailment

**Date:** 2026-10-11 · **Phase:** 7 · **Status:** accepted, implemented
2026-10-11, **measured the same day** (see *Measured*) · **Amends:** ADR 0016 (only
amounts are reconciliation obligations), ADR 0018 (a claim that is nothing but
a date keeps the original behaviour)

## Context

`citation_accuracy` fell from 0.867 (08-13 baseline) to 0.782 (10-10
baseline). The fall is 0.085, beyond the ±0.037 judge noise ADR 0039 measured,
and CLAUDE.md recorded it as unexplained. Both runs used GLM 5.2 for synthesis
and `claude-sonnet-5` as the judge, so the comparison is like for like.

The diagnosis cost nothing. Each report's `citation_judgments` was joined to
the claim rows of its recorded runs (`recorded/` for 10-10, the same directory
at `c257082^` for 08-13). The judge rejected 25 of 188 claims on 08-13 and 45
of 206 on 10-10. The 20 extra rejections break down as follows:

| | 08-13 | 10-10 |
|---|---|---|
| All judged claims | 163/188 = 0.867 | 161/206 = 0.782 |
| Without date-only numeric claims | 0.867 | 160/194 = 0.825 |
| Without those and claims about the evidence | 161/186 = 0.866 | 160/188 = 0.851 |

The residual 0.015 is inside the noise.

**Date-only numeric claims: 11 of the 20.** On 10-10, synthesis typed g-024's
dated court events ("On January 16, 2024, the Supreme Court denied both
petitions") as `numeric`. On 08-13 it typed the same events `qualitative`. A
`numeric` claim goes to the reconciler. `XbrlNumericReconciler` has no amount
to look up, so it hands the claim to the cited-text matcher. That matcher
drops day-of-month numbers as obligations, *except* when nothing else is left
(ADR 0018 kept that case on purpose). It then grounds the claim on the first
cited number equal to the day. It never checks the month, the year, or what
happened.

The cleanest case is claim-2-13 in run `e703c3f5d4eb`: "On April 30, 2025, the
California District Court found Apple to be in violation…" was grounded on a
snippet about **September 30**, 2024. The judge was right to reject these
claims. Each "supporting span" was a 140-character window around a day number.

| | 08-13 | 10-10 |
|---|---|---|
| date-only claims on the reconciler path | 0 | 12 (11 in g-024, 1 in g-017) |
| of those, rejected by the judge | — | 11 |

**Claims about the evidence: 6 of the 20.** Examples: "The provided excerpts
do not include any earnings call transcript content." The semantic verifier
grounded each one on an unrelated span. There were 2 on 08-13, both passed,
and 6 on 10-10, all rejected. This is a synthesis habit, and changing it would
mean changing the prompt, which this repo does not tune for gate metrics. It is
recorded here, not addressed.

## Decision

**A `numeric` claim whose numbers are all days of the month goes to the
entailment judge**, the same path as a `qualitative` claim, and its verdict
carries `method="semantic"`. `states_only_dates()` in `tools/reconcile.py`
makes the call, reusing `parse_numbers` and `_is_date_day` so that "date" means
exactly what it already means to the reconciler. Years are already skipped by
`parse_numbers`.

- A date beside an amount ("As of September 27, 2025, cash was $35,934
  million") still goes to the reconciler. ADR 0016 already removes the day from
  its obligations.
- A count ("lead times of more than 12 months") is not a date and stays on the
  cited-text path, as ADR 0016 decided.
- A `numeric` claim with no number at all is not "only dates". It stays with
  the reconciler, which rules it unsupported as before.

`verifier_eval.py` routes the same way, so its docstring, "the same building
blocks the graph uses", stays true. The labelled set has no date-only numeric
claims, so the `verifier_*` metrics cannot move.

**Rejected: matching the full date in the cited text.** It would stop "April
30" from matching "September 30". But it would still ground the claim on a
date, not on the event, so "On April 30, 2025, the court ruled for Apple" would
pass against a filing that says it ruled against. The entailment judge reads
the whole claim.

**Rejected: re-typing the claim at synthesis.** That changes the prompt, and
the claim type is the model's call. Verification has to be correct for
whatever type it receives.

**This is not tuning for a gate metric.** It closes a hole that let a dated
claim be grounded without its content being checked. It can lower
`grounding_rate`: a date-only claim that entailment rejects is loop-backed and
possibly retracted, where before it was grounded on a day number. That is the
point.

## Tests

- `test_states_only_dates` in `tests/test_reconcile.py` covers the boundary:
  an event date, a fiscal year-end, a count, a date beside a figure, and a year
  alone.
- `test_a_numeric_claim_stating_only_a_date_is_judged_by_entailment` in
  `tests/test_graph.py` runs the real graph on the g-024 shape. The date-only
  claim reaches the entailment judge and not the reconciler; the dated figure
  still reaches the reconciler. Reverting the routing makes it fail.

## Consequences

- One more cheap entailment call for each date-only claim, in place of a
  reconciler call that made no model call on this path.
- A cited chunk that really states the event still grounds the claim, and the
  `supporting_span` is now the judge's quotation, not a window.

## Honest limits

- **Measured in one run only** (see *Measured*). The numbers to read are the
  date-only claims' methods (all `semantic`), their verdicts, and
  `citation_accuracy`. It is measured together with the ADR 0046 addendum,
  which touches different claims (omitted keys), so the two stay separable.
- Whether synthesis types events as `numeric` again is up to the model. The
  fall in `citation_accuracy` came from one question on one run. If no
  date-only claims appear in the next run, this change will have done nothing
  measurable, and that is not evidence that it was unnecessary.

## Measured

Run `agent-20261011-165218` on image `64ef36d`, the same afternoon, became the
accepted baseline: 30/30, `degraded_attempts: 0`, `citation_accuracy` 0.849
(185 of 218).

- **The new path fired twice, and both times as intended.** Both date-only
  `numeric` claims were in g-024, both went to entailment (`method: semantic`),
  and both were grounded on the full sentence. One example: "The Ninth Circuit
  affirmed the District Court's ruling on April 24, 2023", with the span "On
  April 24, 2023, the U.S. Court of Appeals for the Ninth Circuit … affirmed".
  No date-only claim reached the reconciler.
- **Most of the recovery is not this change.** This time synthesis typed 13 of
  g-024's events `qualitative`, as it did on 08-13, and only 2 as date-only
  `numeric`, against 11 on 10-10. g-024 had 2 rejections in 16 judged claims,
  against 11 in 19. The fix removes the failure when the typing goes the 10-10
  way. It is not why this reading is high: a reading of 0.849 against 0.782
  mostly measures which way the model typed the claims.
- Windowed spans were judged 7 times and rejected twice, against 17 and 12.
- Claims about the evidence are still produced, as expected: 3, all rejected.
- One run cannot show how often the model types events `numeric`, so it cannot
  show how much the fix is worth on average. What it shows is that when it
  does, the claim's content is now checked.

