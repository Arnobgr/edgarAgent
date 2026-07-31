# ADR 0020 — Instant dates, the fiscal calendar, and what "covered" means

**Date:** 2026-07-31 · **Phase:** 3 (post-gate remediation) · **Status:** accepted ·
**Extends:** ADR 0012 (period-shift protection), ADR 0015 (metric rescope)

## Context

The Phase 3 gate passed on 2026-07-28 with `expected_fact_coverage` 0.393 —
the system declines questions whose figures are in the corpus. Before chasing
that number, the instrument measuring it had to be made trustworthy, because
this project's governing lesson is a metric that reads a number other than
the one it names (ADR 0015). Three things were wrong, and they compound.

**1. Half the facts store had no date at all.** 15,211 of 28,550 facts — every
instant, 53% — carried NULL `period_start` *and* NULL `period_end`.
`ingest/facts.py:44` already intended to capture it:

```python
period_end = _str_or_none(row.get("period_end") or row.get("period_instant"))
```

The 2026-07-30 findings attribute this to a missing column. That is wrong.
`period_instant` exists in edgartools 5.42 and is emitted
(`edgar/xbrl/facts.py:1076,1085`, listed in `to_dataframe`'s column order at
`:924`). The defect is pandas semantics: an instant row's `period_end` cell is
**NaN, not missing**, and NaN is truthy, so the `or` never reaches its
fallback and `_str_or_none` maps the NaN to `None`. That is why the loss is
15,211 of 15,211 rather than partial.

Consequences beyond the missing column: `_own_fiscal_year` exists precisely
so a 10-K's prior-year comparatives do not carry the filing's year and let a
period-shifted claim falsely ground (ADR 0012). It derives the year from
`period_end` and falls back to the filing's year when that is None — which
was **every instant**. The protection was inoperative for the half of the
store it never reached.

**2. Fixing that exposed a fiscal-calendar defect it had been masking.** A
fiscal year is named for the calendar year it *ends* in, so a period end's own
calendar year is not the fiscal year for an off-calendar interim period:
AAPL's quarter ending December 2025 is Q1 **FY2026**, NVDA's ending April 2026
is Q1 **FY2027**. Reading the year verbatim mislabelled **5,838 of 28,550
facts** — every AAPL and NVDA interim period. TSLA (December year-end) was
unaffected, and *annual* figures were always correct because their period end
falls in the year-end month, which is why a 10-K-shaped gold set never caught
it.

**3. The coverage metric was loose in both directions.**

- It resolved each label with `limit=5` and accepted **any** returned
  candidate. For a balance-sheet concept that means any of a fiscal year's
  four balance dates — and for NVDA total assets the five rows it sampled
  (125,254 / 111,601 / 140,740 / 111,601 / 161,148) never contained the
  year-end 206,803, so g-006 could not be scored correct by *any* answer.
- It matched on a flat 0.5% band, which credits a **different** number that
  lands nearby. g-003 stated "the evidence does not include a directly stated
  dollar figure", then derived $18,571M from 8.6% × revenue against a filed
  18,497 — scored as coverage.

**4. Nothing in the gate cost anything for saying nothing.** Every `TARGETS`
entry measures the quality of what was said. A system answering all 30
questions with "I cannot determine that from the filings" has no ungrounded
claims, no surviving hallucinations and no wrong figures: a perfect gate.

**5. ADR 0011's retrieval labels cannot see figure delivery.** They are
`SectionRef` — any chunk in the right section counts — so retrieval can score
well while the chunk carrying the figure never enters evidence, which is the
mechanism behind 13 of the 17 coverage misses.

## Decision

**1. Normalize each side before the fallback**, so an instant's date survives,
and re-extract facts for the whole corpus (SEC network only, no paid API).

**2. Derive the fiscal year through a year-end month per corpus ticker**
(TSLA 12, AAPL 9, NVDA 1; unknown → December, i.e. no shift), in
`_own_fiscal_year` where the docstring already said a fiscal calendar map
would go.

**3. Every instant gold label states its balance date**, and coverage
resolves each label to exactly one value.

**4. Coverage means the *filed* figure was delivered.** It is the only
instrument that detects "the fact was in the corpus and we failed to surface
it"; crediting arithmetic makes it blind to exactly that, and would let a
system that retrieves nothing score well by deriving figures from percentages
it read in prose. The rejected reading — "the analyst got a correct number" —
is already measured by `numeric_fidelity` and the reconciler.

**5. Reuse the reconciler's precision predicate; do not write a second one.**
`matches_at_stated_precision` is extracted from
`XbrlNumericReconciler._value_matches` and used by both. g-015's "$416
billion" against a filed 416,161 is the filed figure at coarser precision and
counts, exactly as ADR 0012 admits it elsewhere; g-003's derived $18,571M does
not. Both metrics that read figures out of an answer go through
`delivered_prose()`, so neither can credit a figure surviving only in the
retraction disclosure or inside a chunk id.

**6. Score `filed` / `derived` / `absent` / `unresolved`; gate on `filed`.**
A strict gate plus the breakdown keeps the diagnostic a lenient metric would
destroy; a lenient metric with no breakdown is the worst of the options.

**7. `expected_fact_coverage` joins the accepted baseline**, so
`--write-baseline` refuses a run that drops it.

**8. Figure-level retrieval labels, generated free.** For every fact in
`ingest.db`, the chunks whose text prints its filing-scale mantissa are the
gold set for "query → the chunk carrying this figure". No hand labelling, no
LLM, no network. Mantissas need four significant digits: the 3-digit billions
form matches arbitrary filing text and produced two spurious "the figure was
available" readings during the diagnosis.

**9. Balance-date perturbations join the verifier claim set** so the
false-accept class the dates open is measured rather than assumed (closed in
ADR 0021).

## Consequences

**The store.** 78/78 filings re-extracted, 28,550 facts before and after, no
`(accession, concept)` row loss, 0 NULL dates. NVDA total assets resolve to
206,803 at FY2026 year-end (25 Jan 2026) with 111,601 re-dated to FY2025 and
the FY2026 quarters correctly its own.

*The UNIQUE-key hazard the plan anticipated does not exist.* `period_start`
stays NULL for instants and SQLite treats NULLs in a unique key as distinct,
so the constraint remains inert for them — verified both by argument and by
the unchanged row count. The ADR 0019 blast radius does not apply.

**The metric,** re-scored on the accepted run's own answers recovered from
checkpoints (free, 30/30 recovered):

| | value |
|---|---|
| old metric, old store (the report's number) | 0.393 |
| old metric, post-fix store | 0.429 |
| **new metric** (filed only, stated precision) | **0.357** |
| buckets | filed 10, derived 2, absent 16, unresolved 0 |

Two labels move for measurement reasons alone, and both are corrections to
the 2026-07-30 findings:

- **g-008 is now `filed`.** `us-gaap:CashAndCashEquivalentsAtCarryingValue` at
  2025-09-27 *is* 35,934 — the answer's figure was right, and the dateless
  store had offered 30,299/29,943 as FY2025 candidates. The findings read this
  as the model's period/definition confusion; it was the metric's.
- **g-019 TSLA lands in `derived` on a coincidence.** "$137.4 billion" is an
  unrelated difference figure falling inside 0.5% of the filed 137,806. It is
  not credited, and it is direct evidence that the flat tolerance was scoring
  noise.

**Figure delivery.** 175 headline annual figures, production configuration
(openai, RRF, no rerank):

| recall@5 | recall@8 | recall@10 | recall@20 | recall@50 |
|---|---|---|---|---|
| 0.274 | **0.354** | 0.400 | 0.520 | 0.617 |

`recall@8` is the one that matters — `retrieval_k` is 8. Two independent
instruments now agree on the same failure: figure delivery 0.354 and
`expected_fact_coverage` 0.357. The set reproduces the 2026-07-30 ablation's
production ranks exactly (g-001 at 10, g-002 at 12, g-009 at 20 and 21), which
is what makes it usable as a before/after.

**The threshold: 0.65, from one measurement.** A 10-question subset run on
2026-07-31 (after Stage C landed) took its 14 labels from 0.286 to **1.000**
— filed 14, derived 0, absent 0, unresolved 0. Holding the 14 unmeasured
labels at their old buckets puts a **floor of 0.714** on the full set. The
target sits below that floor, because the unmeasured half is the harder
cross-company work, and far above both the pre-fix 0.357 and the 0.000 a
declining system scores. It should rise once a full baseline has measured all
28 — a target set from a subset is a floor, not a ceiling.

**Cost of the calendar map.** 5,838 rows change fiscal year, all AAPL and NVDA
interim periods. No gold expected fact and none of the 112 labeled verifier
claims is quarterly, so no gated metric moves on this alone. It does mean the
store's quarterly labels *were* wrong for two of three tickers before today,
which retroactively casts doubt on any quarterly claim the system verified
before 2026-07-31.

## Honest limits

- The strict-mantissa rule has a known blind spot: a figure a chunk renders at
  ≤3 significant digits ("$94.8 billion") reads as absent from the labels.
- Gold wording in the figure set is production-accurate only for single-part
  questions, which pass through the decomposer verbatim; multi-part rows
  approximate, the same approximation the findings' ablation made.
- The `derived` bucket inherits the flat 0.5% band and is coincidence-prone
  (g-019 above). It is diagnostic only and never gates.
