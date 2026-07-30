# Evidence: recall decomposed, and retraction's collateral excision

**Date:** 2026-07-30 · **Status:** evidence + **verdicts on the 2026-07-28
findings** · remedies are **not** here — they are
`plans/phase-3-recall-and-retraction.md` · **Source run:**
`eval/reports/agent-20260728-212914.json` (the accepted Phase 3 baseline,
unchanged)

## Why this document exists

`docs/findings/2026-07-28-answer-recall-and-claim-staleness.md` recorded two
issues found after the gate passed and deliberately stopped at evidence. It
left one question open by name — *"Direction of the corpus gap is unknown …
does not establish whether the cause is uniform"* — and proposed a mechanism
for the second issue on circumstantial grounds.

This document closes both. **Issue 1 is confirmed and decomposes into three
causes. Issue 2's symptom is real but its stated mechanism is wrong, and its
severity is misstated in both directions.** A third defect sits underneath
both; it is a *known accepted deferral* whose consequences are larger than
recorded.

Everything below came from artifacts already on disk plus ~55 embedding calls
for the retrieval ablation (well under a cent). No paid eval run, no LLM
calls. Scripts in the appendix.

## Verdicts on the 2026-07-28 claims

| claim | verdict |
|---|---|
| System declines questions whose facts are in the corpus | **confirmed** |
| `expected_fact_coverage` 0.393, 0 of 28 labels unresolvable | **confirmed** |
| Traced to retrieval not surfacing the figure | **confirmed for 13 of 17 misses**; 4 are downstream of retrieval |
| No ingest gap, no embedding gap, no label bug | **confirmed** — 0 of 17 figures absent from the corpus |
| ADR 0011 never covered end-to-end figure delivery | **confirmed** (and answered in the plan's scope guards) |
| `claims` channel carries claims describing an earlier draft | **refuted** — the channel has no reducer; it is replaced wholesale |
| The warning is not evidence of surviving hallucinated text | **confirmed, and stronger**: 4 of 4, not 3 of 4 |
| `hallucination_rate` is pessimistic, not optimistic | **confirmed** — the honest value is 0.000, not 0.0145 |
| ~10% of the scored population is stale | **refuted** — 3.6%; 21 of the 31 orphans are correctly-retracted claims |
| `ProductsRevenue` cannot match label `Products` via `LIKE` | **confirmed** |

---

# Issue 1 — recall decomposes into three causes, not one

## Method

For every gold question the exact synthesis context of the final draft was
reconstructed by replaying `build_synthesis_messages` over the final
checkpoint's `evidence` and `sub_questions` (nothing writes `evidence` after
the last `synthesize`, so the final state *is* what that call saw). Each
expected figure was then searched for in three places: the packed context,
`state.evidence`, and every chunk file on disk.

Presence uses **strict mantissas** — filing-scale renderings with ≥4
significant digits (`94,827`, `344,085`), comma and bare. The 3-digit
billions form false-positives badly: `161` for `161,148,000,000` matches
arbitrary filing text, and it produced two spurious "figure was available"
readings before the rule was tightened. **Known blind spot:** a figure
rendered at ≤3 significant digits in a chunk would read as absent.

## Result

| where the figure was | n | cause |
|---|---|---|
| in the corpus, never entered `evidence` | **13** | retrieval |
| in `evidence`, dropped before the model saw it | **2** | context packing |
| in the packed context, answer still omits it | **2** | synthesis |
| absent from the corpus | **0** | — |

The doc's diagnosis therefore holds for the dominant cause, and generalises
from g-001 to 13 questions. The other 4 would survive any retrieval fix:

- **Packing (2):** g-016 (NVDA R&D `18,497`) and g-018 (AAPL net income
  `112,010`) were retrieved into `evidence` and cut by the
  `chunks_per_sub_question = 8` fan-in cap before reaching the window.
  `state.evidence` held 59 and 26 chunks respectively.
- **Synthesis (2):** g-008 (AAPL cash `30,299`/`29,943`) and g-019 (AAPL
  assets `364,980`) had the figure in the window and declined anyway. Both
  are balance-sheet instants, and both refusals turn on period/definition
  confusion — g-008 says *"the evidence provided does not separately disclose
  a 'cash and cash equivalents' figure (excluding restricted cash)"* and
  answers with `$35,934M`, the restricted-cash-inclusive cash-flow figure.
  This is downstream of Issue 3.

## Why retrieval misses — ablation over the 13

Rank of the first chunk containing the figure, within 50 fused candidates.
Production consumes the top `retrieval_k = 8`; `—` means not in the top 50.

| case | prod | no filter | dense only | sparse only | statement-vocab rewrite |
|---|---|---|---|---|---|
| g-001 TSLA revenue FY25 | 10 | 47 | 12 | 9 | **4** |
| g-002 AAPL net income FY25 | 12 | 19 | 7 | 7 | **7** |
| g-006 NVDA assets FY26 | — | — | — | — | — |
| g-009 TSLA revenue FY23 | 20 | 26 | 13 | 45 | **2** |
| g-009 TSLA revenue FY24 | 21 | 26 | 13 | 45 | **3** |
| g-010 AAPL R&D FY25 | — | — | 27 | — | **10** |
| g-010 AAPL R&D FY23 | 26 | 27 | 14 | — | **7** |
| g-015 TSLA revenue FY25 | — | — | — | — | **4** |
| g-016 TSLA R&D FY25 | 31 | 30 | 15 | 48 | **8** |
| g-016 AAPL R&D FY25 | — | — | — | — | **10** |
| g-018 AAPL net income FY25 | — | — | 48 | — | **7** |

Four readings, one of them a useful negative:

1. **`k = 8` is never enough.** Ranks cluster at 10–31. Raising `k` alone:
   `k=20` → 3 of 11, `k=30` → 5 of 11, `k=50` → 6 of 11. A partial
   mitigation, and the most expensive one per figure recovered.
2. **The query-implied filter is helping, not hurting.** Removing it makes
   things worse (10 → 47, 12 → 19, 20 → 26). The obvious hypothesis — that
   `infer_filter`'s `fiscal_year` condition excludes the annual filing — is
   **wrong**, and worth recording so nobody spends a day on it. The FY2025
   10-K's `fiscal_year` payload is correctly `2025`.
3. **Query formulation is the dominant lever.** Filing vocabulary
   ("consolidated statements of operations", "total revenues", "annual")
   puts **10 of 11 inside the top 10** — ranks 2, 3, 4, 4, 7, 7, 7, 8, 10,
   10. In code, `research` retrieves with `query = state.query or
   sub_question.text` (`graph/build.py:176`), i.e. the raw analyst phrasing.
   **No query-formulation stage exists anywhere in the system.**
4. **BM25 dilutes the fusion for this query class.** Dense-only outranks the
   RRF fusion in 7 of 11 cases (g-002 7 vs 12, g-009 13 vs 20, g-010 27 vs
   —, g-018 48 vs —). Sparse matching on "total"/"revenue"/"fiscal" spreads
   across the whole corpus.

g-006 fails under every configuration for a different reason — see Issue 3.

## The architectural asymmetry

Every one of the 28 expected figures resolves in the XBRL facts store:
`query_facts` returns rows for 28 of 28. The store is wired into `verify`
(the reconciler) and **not** into `research`.

**The system can check a number it cannot look up.** `plans/00-overview.md`
§3.3 decided that numeric truth lives in XBRL rather than in parsed HTML
cells; only the verification half of that decision was ever implemented. The
13 retrieval misses are all standard flow facts — revenue, net income, R&D —
sitting in a local SQLite table the answering path cannot reach.

## The measuring instrument is loose in both directions

Two of the 11 "covered" facts were credited without the figure being
delivered as a filed value:

- **g-003** (NVDA R&D FY2026): the answer states *"the evidence does not
  include a directly stated dollar figure"*, then derives *"implies R&D
  spending of approximately $18.6 billion ($18,571 million)"* from 8.6% ×
  revenue. Against the filed `18,497`, that is inside the metric's 0.5%
  tolerance. A **derived approximation** scored as coverage.
- **g-015** (AAPL revenue FY2025): `$416 billion` against filed `416,161` —
  a 3-significant-digit rounding, also inside tolerance.

These two are not the same kind of looseness, and a corrected metric must
separate them: g-015 *is* the filed figure, stated at coarser precision, which
is exactly what ADR 0012's "matches at the claim's stated precision" rule
already admits elsewhere in the system. g-003 is a **different number**, reached
by arithmetic, landing near the filed one. The flat 0.5% tolerance cannot tell
those apart because it does not consult the claim's stated precision at all.

So `0.393` is measured with a fuzzy instrument. Combined with Issue 3's
instant-label problem, it is not yet fit to gate anything.

---

# Issue 2 — the mechanism is collateral excision, not staleness

## The stated mechanism is refuted

The 2026-07-28 doc attributed the orphans to claims outliving their draft:
*"The `claims` channel still carries claims describing the earlier draft."*

`claims` is a plain channel — **no reducer** (`state/__init__.py:209`) —
so each `synthesize` return replaces the list wholesale. Two measurements
confirm it behaves that way:

- Across all 30 runs, **no final claim list mixes claim-id generations.**
  Every question's surviving claims share one `claim-<draft_no>-*` prefix.
- The final claim count tracks the last synthesis, not the sum of passes
  (g-011: 25 → 35 → 31 claims across three syntheses; final list is 31).

Claims do not outlive their draft. The correlation the doc observed with
loop-back counts is real but has a different cause.

## What actually happens

`retract` (`graph/build.py:320`) iterates unsupported claims and calls
`excise_claim`, which deletes **one whole sentence** per claim, chosen by
**best token overlap ≥ 0.5** (`graph/retract.py:MIN_OVERLAP`), over a
**draft it mutates as it goes**. These claims overlap heavily in token space
— g-011's are all of the form "Data Center revenue was $X billion, up Y%
year-on-year" — so a claim's best match is frequently the sentence stating a
*different* claim.

Diffing the last pre-retract draft against the final answer, all four
stranded claims' text was **present before and gone after**:

| claim | overlap pre-retract | overlap post-retract |
|---|---|---|
| g-011 `claim-4-22` | **1.00** | 0.38 |
| g-011 `claim-4-29` | **1.00** | 0.40 |
| g-013 `claim-4-5` | 0.88 | 0.25 |
| g-013 `claim-4-6` | 0.73 | 0.33 |

They were removed — by *other* claims' excisions — and the loop then failed
to find text it had already deleted. g-011 shows the collision directly: one
removed sentence carried 16 claims above the 0.5 threshold, another 14.

**The 2026-07-26 rule is correct; its failure detection is not.** Keeping a
claim `unsupported` when excision genuinely fails is right (it stops
surviving hallucinated text from hiding). What is broken is concluding that
excision failed when the sentence was already gone.

## Corrected severity

**Scored-orphan rate is 3.6%, not ~10%.** Of the 31 orphans (best overlap
with the final answer body < 0.5):

| status | n | reading |
|---|---|---|
| `retracted` | **21** | working as designed — removed from the answer *and* excluded from the scored population |
| `grounded` | 6 | a defect, below |
| `unsupported` | 4 | the collateral-excision cases above |

`build_run_result` routes `status == "retracted"` to `retracted`, not
`grounding`, so the 21 were never in the scored population. Orphans inside
the 275 scored claims: **10, i.e. 3.6%.**

**`hallucination_rate` 0.0145 is entirely artifact.** All four numerator
claims' text is absent from the delivered answer, so surviving hallucinated
text in the accepted run is **0 of 275**. The doc estimated "~1 has text in
the delivered prose"; measurement says none does. The user-facing warning —
*"they may still appear above — treat them as unverified"* — is false in 4
of 4 cases.

**New defect: verified content is being silently deleted.** Three
`grounded` claims lost their home sentence to another claim's excision:

| claim | overlap pre-retract | overlap post-retract |
|---|---|---|
| g-011 `claim-4-7` | **1.00** | 0.36 |
| g-011 `claim-4-25` | 0.94 | 0.35 |
| g-016 `claim-4-8` | 0.83 | 0.33 |

The grounding table still lists these as delivered, verified claims. A
reader gets an answer with verified content removed, no disclosure, and a
table advertising sentences that are not in the prose. This is the most
user-visible consequence of the issue and the 2026-07-28 doc did not
identify it.

**Scale.** 11 of 30 runs ran a retraction pass; across them **26 of 204
answer sentences (12.7%) were deleted**. g-011 lost 15 of 35. Orphan rate
rises monotonically with loop count — 0/119 at `verify=1,critic=1`, 8/83 at
`verify=2,critic=1`, 23/108 at `verify=3,critic=2` — because more loop-backs
mean more unsupported claims competing for the same sentences.

## Root cause

The claim → sentence mapping is **re-derived lexically at retract time**
instead of captured at synthesis. The synthesizer wrote both the prose and
the claim list and knows the correspondence exactly; `DraftClaim` carries
`text`, `claim_type` and `source_chunk_ids` but no span or offset, so the
mapping is discarded and guessed later by token overlap. The synthesis
prompt compounds it by asking for claims "restated as one self-contained
sentence", which is not the same object as a sentence of the answer.

Two smaller contributors in the same function: `score >= best_score` lets
later sentences win ties arbitrarily, and no bookkeeping prevents one
sentence from being the target of several claims.

## Confirmed: the `LIKE` direction bug

`query_facts` builds `concept LIKE ? OR label LIKE ? OR REPLACE(label,' ','')
LIKE ?` with the pattern `%<term>%`. A mapper term longer than the filed
label can therefore never match: `'%ProductsRevenue%'` against label
`Products` is unsatisfiable by construction. ADR 0018 bridged a *space*
difference (`NetSales` → "Net sales"); it cannot bridge a suffix the filed
label does not carry. g-013 `claim-4-6` is arithmetically correct
((316,199 − 298,085)/316,199 = 5.73%) with both operands filed, and was ruled
unsupported on this.

---

# Issue 3 — instant facts carry no date at all

`plans/phase-3-metric-diagnosis.md` records "store ingest gaps (`period_end`
NULL for instants …)" as an **accepted deferral**, so the gap itself is
known. Four things about it are not recorded, and each is load-bearing.

**1. The date is lost entirely, and it is half the store.**

| kind | rows | no `period_end` | no `fiscal_period` |
|---|---|---|---|
| duration (flow) | 13,339 | 0 | 289 |
| instant (balance-sheet) | **15,211** | **15,211** | **15,211** |

Instants have neither `period_start` nor `period_end` — both are **NULL**, not
empty. An XBRL instant fact has exactly one date; the pipeline persists none
of it, leaving `fiscal_year` as the only temporal key for **53% of the facts
store**. Flows are clean.

The extraction already *intends* to capture it —
`ingest/facts.py:44` reads `row.get("period_end") or row.get("period_instant")`
— so neither key exists in this edgartools version's fact dataframe and the
fallback silently yields `None`. This is a wrong column name, not a missing
design. (The correct name cannot be discovered offline: `data/parsed/` holds
sections and tables, not the fact dataframe, so the facts stage must re-fetch
XBRL from SEC — free, no API keys.)

**2. It disables the ADR 0012 period-shift protection for half the store.**
`_own_fiscal_year` (`ingest/facts.py:66`) exists precisely because
"edgartools stamps every fact with the *filing's* fiscal year, so the
prior-year comparatives in a 10-K would carry the wrong year — which would
let a period-shifted claim falsely ground". It derives the correct year from
`period_end` and **falls back to the filing's reported year when
`period_end` is None** — which is every instant. So the protection is
inoperative for 15,211 facts: NVDA's `111,601` balance, dated 26 Jan 2025,
is stored as `fiscal_year = 2026` because it appears as the comparative in
the FY2026 10-K. The defect the function was written to prevent is live in
the half of the store it never reaches.

**3. It opens an unmeasured verifier false-accept class.** With no date to
compare, `_period_admissible` admits *any* same-fiscal-year instant
(`tools/reconcile.py:613`, deliberate under ADR 0016). NVDA `us-gaap:Assets`
FY2026 is 9 rows and 6 distinct values — 111,601 / 125,254 / 140,740 /
161,148 / 206,803 — so a claim stating total assets of `$125,254M` at FY2026
year-end grounds, though that is a quarterly balance and year-end is
`206,803`. All 16 `period`-perturbed claims in `eval/verifier_claims.jsonl`
are *flow* concepts with shifted fiscal years; **none perturbs a balance date
within a year.** Verifier P/R 1.000 does not cover this class.

Related, and benign today: because SQLite treats NULLs in a UNIQUE key as
distinct, the facts `UNIQUE` constraint is **inert for instants** — the two
balance dates a filing reports survive only because their key collides on
nothing. Populating the date makes the constraint active for instants for the
first time, which is correct but is the ADR 0019 blast radius again.

**4. It corrupts the recall metric for instants.**
`expected_fact_coverage` calls `query_facts(..., limit=5)` and accepts any
returned candidate. For NVDA FY2026 assets the 5 rows it saw were 125,254 /
111,601 / 140,740 / 111,601 / 161,148 — **the true year-end 206,803 was never
among them**, so the question could not be scored correct by any answer.
Coverage splits accordingly: flows 10/23 = 0.435, instants 1/5 = 0.200. Four
of the 17 misses (g-006, g-008, g-019 ×2) are partly measurement artifact,
and both synthesis-side misses are instants.

---

# Risks

1. **The gate suite still measures precision only.** Unchanged from
   2026-07-28 and now quantified: a refusal scores perfectly on
   `grounding_rate`, `hallucination_rate` and `numeric_fidelity`. The only
   recall-style signal is not a gate and is not yet trustworthy.
2. **Two gate metrics will move for bookkeeping reasons alone.** Correctly
   labelling the 4 collateral-excision claims `retracted` removes them from
   the scored population: `hallucination_rate` → 0.000 and `grounding_rate` →
   257/271 = 0.948, with no change in system behaviour. **`0.000` is
   precisely the value CLAUDE.md flags as an artifact of the pre-2026-07-26
   retraction bug.** Any future session reading a 0.000 must be able to tell
   the two apart; the pre/post-retract diff and a post-retract invariant test
   are what distinguish them.
3. **Fixing collateral excision naively can fail the gate.** Refusing to
   delete a sentence that hosts a grounded claim leaves the unsupported claim
   in place, moving claims from `retracted` to `unsupported` and pushing
   `hallucination_rate` *above* 0.02. The interim fix must change the
   bookkeeping, not what gets deleted.
4. **Instant-fact recall cannot be fixed before the dates exist.** A
   structured answer path resolves 23 flow facts unambiguously and 5 instants
   to a set it cannot disambiguate. Issue 3 therefore gates part of Issue 1 —
   the sequencing is forced by the data, not chosen.
5. **The strict-mantissa test has a known blind spot** (≤3 significant
   digits), so "never retrieved" for a figure a chunk renders as "$94.8
   billion" would be a false negative. The 13 refusals are corroborated by
   the answers' own testimony that the figure was not in evidence.

---

# Appendix — reproduction

Offline except the ablation. Scripts live in the session scratchpad; each is
short enough to re-derive from the descriptions here.

**Reconstruct the exact synthesis context** (the load-bearing step for the
Issue 1 taxonomy) — recover the final checkpoint per the 2026-07-28
appendix, then:

```python
from fie.context import build_synthesis_messages
context = build_synthesis_messages(
    vals["question"], vals["evidence"],
    sub_questions=vals["sub_questions"],
    chunks_per_sub_question=settings.chunks_per_sub_question,
)[-1].content
```

Classify each expected fact by searching `context`, then `vals["evidence"]`,
then `data/chunks/*.json`, for strict mantissas (`value/1e6` etc., ≥4
significant digits, comma and bare forms).

**Retrieval ablation.** Needs `make qdrant-up` and keys. Drive
`client.query_points` directly to vary one factor at a time: `prefetch=[dense,
sparse] + FusionQuery(RRF)` for production, `using=DENSE` / `using=SPARSE`
alone for the arms, `filter=None` to ablate `infer_filter`. Target set =
chunks of the right ticker whose text contains a strict mantissa; report the
first target's 1-based rank in the top 50.

**Figure-level retrieval labels, generated free.** The ablation's target-set
construction *is* a label generator: for every `(ticker, concept, period)`
fact in `ingest.db`, the chunks containing its mantissa are the gold set for
"query → the chunk carrying this figure". No hand labelling, no LLM calls,
no network. This is the measurement asset ADR 0011 lacks — its labels are
`SectionRef`, so any chunk in the right section counts.

**Pre/post-retract diff** (the Issue 2 mechanism). Collect distinct
`draft_answer` values from the checkpoint history in ascending
`checkpoint_id` order; the last two are the pre- and post-retract drafts of
the final pass. Score every final claim against each with the excision
tokenizer:

```python
from fie.graph.retract import _split_sentences, _tokens
def best(claim_text, draft):
    body = draft.split("The following claims")[0]
    sents = [s for p in body.split("\n\n") for s in _split_sentences(p)]
    ct = _tokens(claim_text.strip().rstrip("."))
    return max((len(ct & _tokens(s)) / len(ct) for s in sents), default=0.0)
```

`best(pre) >= 0.7 and best(post) < 0.5` is the collateral-excision signature.
Group orphans by `status` before drawing any conclusion about the scored
population — that single split is what moves 9.9% to 3.6%.

**Store probes.**

```bash
sqlite3 data/ingest.db "select
  case when period_start is null or period_start='' then 'instant' else 'duration' end,
  count(*), sum(case when period_end is null or period_end='' then 1 else 0 end)
  from facts group by 1"
sqlite3 data/ingest.db "select concept,label,value/1e6 from facts
  where ticker='NVDA' and concept like '%:Assets' and fiscal_year=2026 order by value desc"
```
