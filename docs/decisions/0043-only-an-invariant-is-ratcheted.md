# ADR 0043 — Only an invariant is ratcheted; every score is floored

**Date:** 2026-08-13 · **Phase:** 6 · **Status:** accepted
· **Relates to:** ADR 0039 (which fixed one instance of this and missed three),
ADR 0038 (`runs_with_no_claims` recorded beside the block), ADR 0025 (a
degraded run is not a baseline), ADR 0015 (the `--write-baseline` gate)

## Context

ADR 0039 diagnosed the mechanism exactly: the baseline ratchet has no list of
gated metrics. `_gate(metrics, baseline_metrics)` walks whatever
`--write-baseline` wrote into the `metrics` block of `eval/baselines.json`, so
**membership there is the gate**, and writing a number into it enrols it
silently. It then drew a conclusion one metric wide — move `citation_accuracy`
out, give it a floor — and left the same defect armed on everything else.

The 2026-08-13 run is what made that visible, because accepting it is what
would have tightened the trap. Its numbers are good: `grounding_rate` 0.980,
`numeric_fidelity` 0.989, `expected_fact_coverage` 1.000. Ratcheting is what
turns good numbers into a problem — the next run must beat *these*, and
`_gate` has no tolerance at all. Any drop, of any size, prints `REGRESSION` and
exits 1.

The arithmetic settles it. The run scored **200 claims**, about **95 figures**
and **28 expected facts**, so on the next reading:

| metric | one unit is worth | ratchet accepting this run |
|---|---|---|
| `grounding_rate` | 0.005 (one claim of 200) | 0.980 |
| `numeric_fidelity` | ~0.011 (one figure of ~95) | 0.989 |
| `expected_fact_coverage` | 0.036 (one fact of 28) | 1.000 |

A threshold one flipped claim clears and a second one fails is not a
regression detector.

**This is not a prediction, and that is the part worth recording.** Replaying
the 2026-08-06 drift run's numbers through `_gate` against the baseline it
actually faced returns:

```
['grounding_rate: 0.955 below baseline 0.972',
 'expected_fact_coverage: 0.893 below baseline 1.000',
 'numeric_fidelity: 0.924 below baseline 0.963']
```

— and that is *before* the `citation_accuracy` delta of 0.023 that ADR 0039
investigated. **Four red lines were on the screen against zero missed floors.
One of them was read.** The other three were attributed to the run rather than
to the instrument, and the run was disqualified on `degraded_attempts` (ADR
0025) before anyone had to decide what they meant. A gate that fires four times
on a run everyone agrees is fine is not measuring the system.

The spread across the four full runs since ADR 0023 is the rest of the
argument:

| metric | readings | spread |
|---|---|---|
| `grounding_rate` | 0.902, 0.972, 0.955, 0.980 | 0.078 |
| `numeric_fidelity` | 0.959, 0.963, 0.924, 0.989 | 0.065 |
| `expected_fact_coverage` | 0.750, 1.000, 0.893, 1.000 | 0.250 |

Nothing on the answer path changed between several of those pairs. A gate
whose tolerance is 0.005 sitting under a metric whose observed spread is 0.078
fails on the measurement, not on the system.

## Decision

**1. `_RATCHETED_PREFIXES` is `("hallucination",)`.** The docstring above it
already stated the test — *a metric belongs here only when a drop in it is
evidence of a worse system rather than a different reading of the same one* —
and applying that test honestly leaves one member.

`hallucination_rate` stays because it is **not a score**. Retraction either
removed every unsupported claim from the delivered prose or it did not; 0.000
is the only value a working system produces, and there is no noise band around
zero to trip over. Ratcheting it says "never worse than the accepted run",
which is strictly stronger than the 0.02 floor in `TARGETS` and costs nothing.

**2. Every scored metric named in the report has an entry in `TARGETS`.** The
six `verifier_*` metrics are added at 0.95. They are the ones that would
otherwise have left the ratchet and gained nothing, which is not de-gating but
un-watching — the second half of the ADR 0039 bug, and the half that would
have let `citation_accuracy` collapse to 0.40 and exit 0.

**3. No floor moved.** This is the finding, not an omission. Every candidate
raise sat above a reading that had already happened: 0.95 on `grounding_rate`
is above the 0.902 of 2026-07-31, 0.85 on `expected_fact_coverage` is above
the 0.750 of the same run, and 0.92 on `numeric_fidelity` leaves 0.004 of
margin under the 0.924 of 2026-08-06. **The floors were never the defect. The
ratchet was.** Raising a floor toward the best reading is the same mistake as
ratcheting, spelled differently, and is how `numeric_fidelity`'s 0.95 came to
refuse a legitimate baseline in ADR 0015.

**4. `--write-baseline` records the whole reading in a `measured` block**, and
stops deciding per metric which side of the gate a number belongs on. That
decision is what ADR 0038 and ADR 0039 each got wrong once. `measured` holds
every scalar including the ratcheted one, so a reader comparing two accepted
baselines sees the same shape both times and never has to know how a metric
was gated to find its value. `runs_with_no_claims` and `citation_accuracy`
move into it from their standalone keys; both were there for this reason.

The file is now two blocks with two jobs: **`measured` is the reading, gating
on nothing. `metrics` is the gate.**

## Consequences

A drift run that scores 0.975 where the baseline scored 0.980 now exits 0 and
prints the number. That is the intended behaviour, and it is a real loss of
sensitivity, stated plainly in the limits below.

The 2026-08-13 run is accepted as the baseline, replacing `20260801-122527`.
Its report is kept with `git add -f` (`eval/reports/` is gitignored), because
`accepted_run_ids` reads the report to build `recorded/` and a baseline whose
report is absent is not reproducible. This closes ADR 0038 §3: the accepted
baseline is now 30 verifiable runs of 30, `runs_with_no_claims: 0`,
`degraded_attempts: 0`.

The baseline was written **from the saved report**, not by re-running the eval
under `--write-baseline`. The same three refusals were applied first — no
dropped question, no degraded attempt, no missed floor — so nothing was
skipped; a second $0.69 run to re-measure numbers already measured is waste,
and re-measuring would have produced *different* numbers, which is the whole
subject of this ADR.

## Honest limits

- **A slow decay is now invisible to the exit code.** Six months of 0.980 →
  0.905 passes every gate one run at a time. The mitigation is that `measured`
  is recorded in each accepted baseline and every report is kept, so the decay
  is *visible* to anyone who looks — but nothing fails, and nobody is made to
  look. This is the same trade ADR 0039 accepted for `citation_accuracy`,
  taken deliberately and on more metrics. The honest fix is a trend over
  several readings rather than a comparison of two, and that is a real piece
  of work with a cost; it is not done here.
- **The 0.95 on `verifier_*` is set from readings that are all 1.000.** Six
  identical perfect readings do not tell you where the noise band is. The
  number is a guess at a coarse floor, chosen so that several flipped labels
  of 128 clear it, and it should move once a reading below 1.000 exists.
- **`hallucination_rate` ratcheted at 0.000 is unforgiving by construction.**
  One surviving unsupported claim in one of thirty answers fails the gate.
  That is intended — ADR 0021 is what it cost to learn that a
  `hallucination_rate` of 0.000 can be an artifact — but it means the metric
  most likely to fail a future run is the one with no tolerance, and the
  correct response to it failing is to check the pre/post-retract diff, not
  to widen the gate.
