# Phase 3 — Post-gate remediation: answer recall and retraction integrity

**Status:** ✅ **COMPLETE. Stages A-D landed 2026-07-31 (ADRs 0020-0024) and
Stage E passed 30/30 on 2026-08-01 (ADR 0025), replacing the baseline.**
Phase 4 is unblocked. Results, deviations and what stays open are at the end
under "Outcome". · **Created:** 2026-07-30 ·
**Blocks:** `plans/phase-4-backend-and-deploy.md` (see "Relationship to Phase
4") · **Evidence:**
`docs/findings/2026-07-30-recall-decomposed-and-collateral-excision.md`

The Phase 3 exit gate passed on 2026-07-28 and the baseline in
`eval/baselines.json` stands. This plan fixes three defects found in that
passing run's own artifacts. **Read the findings document first** — every
number below comes from it and none of it needs re-deriving.

## What is wrong, in one table

| # | defect | measured | where |
|---|---|---|---|
| 1 | System declines questions whose figures are in the corpus | `expected_fact_coverage` 0.393; 13 of 17 misses are retrieval, 2 packing, 2 synthesis, 0 corpus gap | `graph/build.py` research/packing |
| 2 | Retraction deletes sentences other claims depend on | 4 of 4 "could not locate" claims were actually removed; 3 `grounded` claims silently deleted; 12.7% of prose cut across 11 runs | `graph/retract.py`, `graph/build.py:320` |
| 3 | Instant facts carry no date | 15,211 rows (53%) with NULL `period_start`/`period_end`; ADR 0012 protection inoperative there | `ingest/facts.py:44,66` |

## The two constraints that set the order

**A re-baseline costs ~75 minutes and real money, and every fix below moves a
metric.** So there is exactly one paid run at the end, and everything that
could change a number must land before it. Batch, don't iterate.

**The instrument is not yet trustworthy.** `expected_fact_coverage` credits
derived approximations (g-003) and 3-significant-digit roundings (g-015), and
for balance-sheet facts it scores against an arbitrary 5-row sample that in
NVDA's case never contained the right value. Chasing recall against that
metric would repeat the project's governing lesson — a metric that reads a
number other than the one it names. **Stage A therefore comes first, and it
is free.**

Stage A → B → C → D → E. A and B cost no API spend. Stop after any stage and
the tree is still green and shippable.

---

## Stage A — make the instrument trustworthy (free, no API spend)

### A1. Persist the instant date

`ingest/facts.py:44` already reads `row.get("period_end") or
row.get("period_instant")`; neither key exists in this edgartools version, so
every instant gets `None`. Find the real column, populate `period_end` for
instants (leaving `period_start` NULL — that is the natural XBRL mapping),
and let `_own_fiscal_year` do the job it was written for.

The column name cannot be found offline (`data/parsed/` holds sections and
tables, not the fact dataframe), so start by dumping
`xbrl.facts.query().to_dataframe().columns` for one filing — a free SEC
fetch, no API keys.

Then re-extract facts for the whole corpus (free, SEC network only; ADR 0019
did exactly this).

**Two hazards, both real:**
- The facts `UNIQUE` key is currently **inert for instants** because SQLite
  treats NULLs in a unique key as distinct. Populating the date activates it
  for 15,211 rows for the first time. This is the ADR 0019 blast radius —
  verify no rows are lost, per concept and per accession.
- `_own_fiscal_year` will start re-dating instants, so prior-year
  comparatives move to their own fiscal year. That is the fix, but it
  **changes what `query_facts` returns for existing gold labels**, which is
  why A2 follows immediately.

**Checks.** `select count(*) from facts where period_end is null` → 0.
Fact count per `(accession_no, concept)` unchanged or higher, never lower.
NVDA `us-gaap:Assets` at FY2026 year-end resolves to exactly `206,803` with
a January-2026 date, and `111,601` re-dates to FY2025. `make check` green.

### A2. Fix the instant gold labels and the coverage contract

- Give every instant `expected_fact` in `eval/gold_set.jsonl` an explicit
  balance date (now expressible after A1), and re-verify `label_status`.
- Drop the `limit=5`-and-accept-any behaviour in `expected_fact_coverage`
  (`eval/agent_runner.py`): with dates present, an expected fact resolves to
  **one** value. Keep the ADR 0015 rule that unresolvable facts count as
  misses rather than shrinking the denominator.
- **Coverage means the *filed* figure was delivered — decided.** The metric is
  the only instrument that detects "the fact was in the corpus and we failed to
  surface it"; crediting arithmetic makes it blind to exactly that. The
  rejected alternative — "the analyst got a correct number" — is already
  measured by `numeric_fidelity` and the reconciler, so adopting it here would
  leave the retrieval failure unmeasured by anything, and would let a system
  that retrieves nothing score well by deriving figures from percentages it
  read in prose.
- **Reuse the reconciler's precision rule; do not write a second one.** g-003
  and g-015 are different cases and the strict contract must separate them:
  g-015 is the filed figure at coarser precision (credit), g-003 derived
  `$18,571M` from "8.6% of revenue" against a filed `18,497` (miss). ADR 0012
  already defines "matches at the claim's stated precision" — apply that same
  predicate. Two components disagreeing about what "the same figure" means is
  a defect waiting to happen.
- **Score three buckets, gate on one.** Classify each expected fact as
  `filed` / `derived` / `absent` and let the metric count only `filed`. A
  strict gate plus the breakdown keeps the diagnostic that a lenient metric
  would destroy; a lenient metric with no breakdown is the worst of the
  options.

**Checks.** Re-score `expected_fact_coverage` on the accepted run's recovered
answers (free, from checkpoints). Publish the before/after, the per-label
breakdown, and the `filed`/`derived`/`absent` split. Every label resolves; 0
unresolvable. A test asserts g-015 credits and g-003 does not, both via the
shared precision predicate.

### A3. Add a recall metric to the gate — threshold deferred by design

Every entry in `TARGETS` — `grounding_rate`, `hallucination_rate`,
`numeric_fidelity`, `citation_accuracy`, verifier P/R — measures the *quality
of what was said*. None measures whether anything was said. So a system that
answers all 30 questions with "I cannot determine that from the filings"
scores a perfect gate: no claims means no ungrounded claims, no
hallucinations, no numeric errors. It passes. That hole is not hypothetical —
Issue 1 is a milder version of it, and the gate did not notice.

Adding a recall-style entry so declining costs something is the point of this
stage. **The threshold is the only open part, and choosing it now would repeat
ADR 0015's mistake**: `numeric_fidelity` 0.95 was set from ambition above the
~0.9 honest ceiling and then correctly refused a legitimate baseline.

Do not pick a number before A1+A2 report the re-scored value. Today's 0.393 is
the floor; the ablation's 10-of-11 recovery is an extrapolation from a sample,
not a measurement. Then set the target below the measured post-fix ceiling and
above what a declining system scores, and record it as an ADR with the
measured basis, like ADR 0015 did.

**Check.** `TARGETS` contains a recall metric; `--write-baseline` refuses a
run that misses it; a synthetic all-refusals result scores near zero.

### A4. Figure-level retrieval labels, generated free

ADR 0011's labels are `SectionRef` — any chunk in the right section counts —
so it cannot see figure delivery. Generate the missing labels
**automatically**: for every `(ticker, concept, period)` fact in `ingest.db`,
the chunks whose text contains its filing-scale mantissa are the gold set for
"query → the chunk carrying this figure". No hand labelling, no LLM calls, no
network. The findings appendix has the construction; it is what produced the
ablation table.

This is what makes Stage C measurable, and it is the only honest basis for
touching `retrieval_k` (see Scope guards).

**Check.** A committed dataset plus an `eval-retrieval`-style runner
reporting figure-level recall@k. It reproduces the findings' production
ranks (g-001 at 10, g-002 at 12, g-009 at 20).

### A5. Instant-date perturbations in the verifier claim set

`eval/verifier_claims.jsonl`'s 16 `period` perturbations are all flow
concepts with shifted fiscal *years*. Add perturbations that shift a balance
**date within** a fiscal year, so the false-accept class Issue 3 opens is
measured rather than assumed. Expect verifier P/R to *drop* when these land —
that is the measurement working, and A1 is what closes it.

**Check.** New labelled claims exist; verifier P/R re-measured after A1 and
reported per claim type. Any regression is attributable to a named claim.

---

## Stage B — retraction integrity, interim (free, no API spend)

The user decision is **both fixes for Issue 2**: this stage stops the harm
with bookkeeping; Stage D removes the cause.

### B1. Make excision claim-set aware

Today `retract` mutates the draft while iterating, so each `excise_claim`
call re-derives its target by token overlap against an already-edited draft.

- Compute the claim → sentence assignment **once**, over the un-mutated
  draft, as a global assignment: each unsupported claim gets at most one
  sentence, each sentence is claimed by at most one claim, and ties break
  deterministically (`score >= best_score` currently lets later sentences win
  arbitrarily).
- Then delete. A claim whose assigned sentence is gone was **removed**, not
  "not found" — so the four collateral cases become correctly `retracted`.

**Do not** "refuse to delete a sentence that hosts a grounded claim." That
sounds right and fails the gate: the unsupported claim then survives in the
prose and must stay `unsupported`, pushing `hallucination_rate` above 0.02.
Changing what gets deleted is Stage D's job; Stage B changes only the
accounting.

**Checks.** A unit test with two unsupported claims sharing one sentence
asserts one deletion and two `retracted` statuses, no warning logged. On the
accepted run's recovered states, the four stranded claims re-classify to
`retracted` and no claim is logged "could not locate".

### B2. Disclose collateral loss instead of hiding it

Three `grounded` claims lost their sentence and the grounding table still
advertises them as delivered. For a system whose premise is "verified against a
source or explicitly retracted", citing a source for prose the user never saw
is the precise failure it exists to prevent — so the invariant is load-bearing:
**every `grounded`/`partial` claim's sentence is still present; every
`retracted` claim's is gone.**

**Keep the invariant; do not add a `ClaimStatus` member.** The minimum that
stops the output lying is: after excision, a `grounded`/`partial` claim whose
sentence is gone was not delivered, so drop it from the grounding list and log
it. A new `Literal` member would ripple through `build_run_result`, the
`RunResult` contract and every metric denominator — for a state that Stage D
makes extinct, affecting 3 claims in 275, below the noise floor of every metric
it would touch. Note in the ADR that Stage D reduces this to a vestigial guard.

**Detect and correct in the graph; assert only in tests.** A hard assertion in
the graph would have killed the accepted run over those 3 claims. The graph
logs and corrects; the test is where the invariant is enforced absolutely.

**Checks.** A test plants a shared sentence and asserts the invariant holds
after excision, and that the collaterally-removed grounded claim is absent from
the grounding list. Re-scoring the accepted run reports how many claims move,
per metric.

### B3. Fix the `LIKE` direction bug

`query_facts` builds `%<term>%`, so a mapper term longer than the filed label
can never match — `'%ProductsRevenue%'` against label `Products` is
unsatisfiable. Match in both directions (or normalise both sides).

**Check.** A store test asserts `ProductsRevenue` reaches label `Products`.
Re-run verifier P/R on the labelled claims — this touches the reconciler, so
ADR 0016's standing rule applies. g-013 `claim-4-6` should ground.

---

## Stage C — the recall capability

Only after Stage A: C1's exactness depends on A1, and C3 is unmeasurable
without A4.

### C1. The facts store as a research tool *(the differentiator)*

The system can **check** a number it cannot **look up**: `query_facts`
resolves all 28 expected facts, and the store is wired into `verify` but not
`research`. `plans/00-overview.md` §3.3 decided numeric truth lives in XBRL;
only the verification half was implemented.

Give the research path a deterministic fact lookup that injects resolved
facts as evidence carrying their accession provenance. The concept/period
mapping model already exists in the reconciler — reuse it rather than
inventing a second vocabulary.

**Scope discipline:** this is a tool on an existing store, not a subsystem.
If it needs more than a lookup, a formatter and a wiring change, it has grown
past its warrant.

**Checks.** g-001, g-002, g-010, g-016 answer with the filed figure in a
`--limit 4 --skip-judge` smoke run. Qualitative questions are unaffected (no
spurious fact injection). The injected evidence is visible in a Langfuse
trace, per the cross-phase definition of done.

### C2. Query formulation before retrieval

`research` retrieves with `query = state.query or sub_question.text`
(`build.py:176`). `state.query` is the *loop-back override* — the failed
claim's text — so the first pass retrieves with the decomposer's sub-question
phrasing. Either way it is not filing vocabulary, and there is no
query-formulation stage in the system. Filing vocabulary moves 10 of 11 failing
figures into the top 10 (from ranks 10–31 and worse). This serves the narrative
half of the corpus that C1 cannot.

**One rewrite, not a multi-query fan-out — decided.** Our own ablation says one
is enough: a single statement-vocabulary rewrite already clears the bar, so
building variants is speculative work against a measurement that passed. Cost
multiplies badly here — the call is per-branch inside a parallel `Send`
fan-out, and branches multiply again on the verify→research loop-back. And
shipping rewrite plus union together makes the recall gain unattributable to
either.

**The one hedge, decided on A4's curve rather than assumed:** retrieve with
`{raw, rewritten}` unioned. That is still one LLM call — only a second
retrieval call — and it insures against a bad rewrite scoring worse than the
original phrasing. Carry it as an `eval-retrieval` arm; adopt it only if the
curve earns it.

**No new graph node.** The rewrite goes inside `research`, before
`retriever.retrieve` at `build.py:179`. A separate `formulate` node would need
its own channel and would change the state machine; unwarranted for a
per-branch step with no fan-in. Concretely: a cheap role in `Settings.models`
reached via `get_model(...)` (never a model ID at the call site), the prompt in
`context/`, and no guardrail work — `research` is already inside `guarded()` and
`BudgetTracker` picks the cost up as a callback.

**Record the rewritten query in state and the trace.** Without it a retrieval
failure is indistinguishable from a rewrite failure, and the next diagnosis
session pays for that.

**Checks.** Figure-level recall@8 from A4 improves, reported before/after, with
the `{raw, rewritten}` union as a separate measured arm. The prompt lives in
`context/` (never in a graph node). Cost per run measured — this adds a call
per branch, and the fan-out multiplies it. The rewritten query appears in a
Langfuse trace.

### C3. `retrieval_k` and the fan-in cap — measured, not adjusted

Two misses (g-016 NVDA, g-018 AAPL) were retrieved and then cut by
`chunks_per_sub_question = 8`; raising the cap fixes them outright. Raising
`retrieval_k` alone recovers 3 of 11 at `k=20`, 5 at `k=30`, 6 at `k=50` — the
least value per token of anything in this plan.

Change these **only** on A4's figure-level evidence, with the cost delta
recorded. Note the useful negative in the findings: removing the
query-implied filter makes retrieval *worse*, so `infer_filter` is not the
problem and should be left alone.

**Check.** A4's recall@k curve and a measured cost-per-run delta accompany
any change to either value.

---

## Stage D — retraction, structural fix

Removes the cause Stage B only accounted for. This changes the synthesis
contract, so it must land before the re-baseline.

Capture the claim → span mapping **at synthesis**, where it is known, instead
of re-deriving it lexically afterwards: add a quoted-span (or offset) field to
`DraftClaim`, validate that the span is a substring of `answer`, and repair by
retry when it is not. `retract` then excises that span deterministically — no
overlap heuristic, no collateral deletion, no false "could not locate".

The synthesis prompt currently asks for claims "restated as one self-contained
sentence", which is a different object from a sentence of the answer. That
wording and the span requirement have to be reconciled.

**Honest limits.** Models quote imperfectly, so the validation-and-repair
loop is load-bearing; unlike today's failure it is *detectable* (a substring
check) rather than silent. Retraction stays deterministic code — an LLM
rewrite would reintroduce exactly the compliance-trust problem
`graph/retract.py`'s docstring rejects, and that reasoning still holds.

**Checks.** A test asserts every emitted claim's span is a substring of the
answer, and that a non-quoting model triggers repair. `MIN_OVERLAP` and the
token-overlap path are gone or demoted to a fallback with a logged counter.
The Stage B invariant now passes trivially.

---

## Stage E — one re-baseline

```bash
make qdrant-up
uv run eval-agent --limit 2 --skip-judge     # smoke first; it has paid for itself twice
uv run eval-agent --write-baseline           # ~75 min, real money
```

**Expect these movements, and check them rather than trusting them:**

| metric | now | expected | why |
|---|---|---|---|
| `hallucination_rate` | 0.0145 | **0.000** | B1 re-labels 4 artifacts; no behaviour change |
| `grounding_rate` | 0.9345 | ~0.948+ | same 4 claims leave the scored population |
| recall metric (A3) | 0.393 | up | C1/C2; and A2 changes its definition |
| verifier P/R | 1.000 | may drop then recover | A5 adds a class A1 closes |
| `numeric_fidelity` | 0.9256 | ~flat | untouched |

**`hallucination_rate` 0.000 is exactly the value CLAUDE.md flags as an
artifact of the pre-2026-07-26 retraction bug.** A future reader must be able
to tell the two apart, so the write-up must cite the pre/post-retract diff and
the Stage B invariant as the distinguishing evidence. If it reads 0.000 *and*
the invariant is not asserted in the graph, treat it as suspect.

**Exit gate for this plan:** 30/30, every `TARGETS` entry met including the
new recall metric, verifier P/R re-measured with A5's claims, `make check`
green, ADRs written, and the accepted baseline replaced deliberately (not with
`--force`).

---

## Scope guards

**ADR 0011 is not being re-opened, and here is the answer to the question the
2026-07-28 findings raised.** ADR 0011 measured 50 queries against
`SectionRef` labels — any chunk in the right section counts — and never
covered end-to-end figure delivery. Its mandate does not reach this work.
Adding a facts-store tool (C1) and a query-formulation stage (C2) are **new
capabilities, not parameter tuning**. `retrieval_k` and the fan-in cap (C3)
*are* the knobs the standing guard is about: change them only on A4's
figure-level measurements, never to move a gate number.

**Do not tune prompts or models for the gate.** C2 adds a prompt that did not
exist; that is not the same as tuning synthesis for a metric. Model selection
stays settled (GLM arm on cost, `eval_judge` on a strong stable model).

**Do not raise a tolerance to move a metric.** A2 tightens the coverage
contract; loosening it would buy the same coincidental credit ADR 0015
diagnosed in `numeric_fidelity`.

**Diagnose from checkpoints, never re-run.** Every number in the findings was
recovered offline from `data/checkpoints.db`. Any Stage A or B claim can be
re-scored on the accepted run's artifacts for free before spending on Stage E.

## ADRs this plan owes

1. **Instant fact dates** — the column, the re-extraction, the UNIQUE-key
   activation, and what re-dating comparatives does to ADR 0012's protection.
2. **Recall in the gate** — the metric's contract (filed-only coverage, the
   reconciler's precision predicate reused, the `filed`/`derived`/`absent`
   split) and the measured basis for its threshold. Extends ADR 0015.
3. **Facts store in the research path** — the architectural half of
   `00-overview.md` §3.3 that was never built, and why a tool beats widening
   retrieval. Covers C2's single-rewrite decision and why no graph node was
   added.
4. **Claim spans and retraction semantics** — span capture, the repair loop,
   why collateral loss is dropped-and-logged rather than given a
   `ClaimStatus` member, and the retirement of token-overlap excision.
5. **Guardrail aborts in the eval harness** (unplanned, written at Stage E) —
   why a node abort returns rather than raises, the measured stall rate, and
   why the result is kept rather than dropped. Extends ADR 0015.

## Relationship to Phase 4

These are engine defects, not deployment blockers, and the Phase 4 API surface
depends on none of them. Two tracks can run in parallel:

- **Now, safely:** the FastAPI skeleton, Dockerfile, and especially the CI
  workflow (Phase 4 §5) — CI is what catches regressions *during* this work,
  so building it first pays for itself. `main` is **33 commits behind `dev`**
  (still at Phase 0); merge before CI assumes a sane default branch.
- **Not until this plan's exit gate passes:** the VPS deploy, the public
  tunnel, and any demo. A service whose headline behaviour is "the evidence
  does not contain Tesla's total revenue" is trustworthy and useless, and
  `expected_fact_coverage` 0.393 is now the baseline that would ship.

## Decisions taken 2026-07-30

1. **A2** — a derived figure does **not** count as coverage. Coverage means the
   filed figure was delivered, matched with the reconciler's own precision
   predicate, and scored as `filed`/`derived`/`absent` with the gate counting
   only `filed`.
2. **B2** — collateral loss is **dropped from the grounding list and logged**,
   not given a `ClaimStatus` member. Detect-and-correct in the graph; the hard
   assertion lives in the test.
3. **C2** — **one** rewritten query, no multi-query fan-out, no new graph node.
   `{raw, rewritten}` union carried as a measured `eval-retrieval` arm only.

## Still open — deliberately

1. **A3** — the recall threshold. Deferred by design until A1+A2 report the
   re-scored value; choosing it in advance is the ADR 0015 failure mode.
2. **C3** — `retrieval_k` and the fan-in cap, movable only on A4's figure-level
   recall curve with the cost delta recorded.

---

## Outcome (2026-07-31)

Stages A-D landed. ADRs 0020 (the instrument), 0021 (retraction integrity and
fact matching), 0022 (facts store in research, query formulation) and 0023
(claim spans) carry the reasoning; this section records what the plan got
wrong and what it left open.

### Measured

| | before | after |
|---|---|---|
| `expected_fact_coverage`, strict | 0.357 | **1.000** — all 28 labels on the full set |
| figure delivery, recall@8 | 0.354 | **0.451** |
| verifier P/R incl. the new balance-date class | 0.889 | **1.000 / 1.000** |
| dateless facts / wrong fiscal year | 15,211 / 5,838 | **0 / 0** |
| `citation_accuracy`, same 10 questions | 0.623 | **0.741** (0.370 as first measured; ADRs 0022, 0024) |
| cost per run, same 10 questions | $0.041 | **$0.014** — C2's extra call is repaid by the loop-backs it prevents |

### Stage E — the accepted baseline (`agent-20260801-122527`, 30/30)

| | 2026-07-28 | **2026-08-01** | target |
|---|---|---|---|
| `grounding_rate` | 0.935 | **0.972** | ≥ 0.90 |
| `hallucination_rate` | 0.015 | **0.000** | ≤ 0.02 |
| `numeric_fidelity` | 0.926 | **0.963** | ≥ 0.85 |
| `expected_fact_coverage` | 0.357 (re-scored) | **1.000** | ≥ 0.65 |
| `citation_accuracy` | 0.685 | **0.851** | not gated |
| verifier P/R | 1.000 | 1.000 | — |

`unanswerable_declined` 1.000, 208 citations judged with 0 judge failures,
`degraded_attempts` 0.

**The 0.000 was verified before it was accepted**, as this plan's Stage E
demanded: 8 claims retracted, every one carrying an `answer_span`, **0** of
those spans still present in the delivered prose, and no scored row with
verdict `unsupported` or `None` in 217. This is the real reading, not the
pre-2026-07-26 artifact.

Two attempts preceded it. The first was killed by the harness and recovered
free from checkpoints (24/30, all targets passed). The second was **refused
by the guard** at 29/30 with `hallucination_rate` 0.049 — two synthesize
calls had stalled at the 600s cap and scored directly, because a guardrail
abort returns a degraded `RunResult` rather than raising and `_run_gold`
retried only on exceptions. ADR 0025 fixes that. It is worth being plain
that the accepted run had `degraded_attempts: 0`, so the retry never fired
and is proven by its tests, not by this baseline.

### Where the plan was wrong

1. **A1's root cause.** Not a missing edgartools column — `period_instant`
   exists. An instant's `period_end` cell is NaN, and NaN is truthy, so
   `a or b` never reached its fallback.
2. **A1's UNIQUE-key hazard does not exist.** `period_start` stays NULL for
   instants and SQLite treats NULLs in a unique key as distinct, so the
   constraint was never activated. Row counts confirm it: 28,550 either side.
3. **"A1 is what closes" the balance-date false accepts.** It does not. A1
   supplied the date; nothing consulted it. Closing the class needed a
   reconciler change (ADR 0021), taken with the operator's decision.
4. **A fourth defect the plan never saw**, surfaced by fixing A1: a fiscal
   year is named for the year it *ends* in, so 5,838 facts — every AAPL and
   NVDA interim period — carried a year one too low.
5. **C1 needed more than "a lookup, a formatter and a wiring change".** The
   first smoke delivered 2 of 8 expected figures: the plumbing worked and the
   row selection did not. Three fixes (term-at-a-time matching, a period-free
   retry, the corpus boundary in the prompt) took it to 28 of 28.

### Decided by measurement, not taste

- **The `{raw, rewritten}` union is rejected** — worse than the rewrite alone
  at every depth (0.423 vs 0.451 at k=8).
- **`retrieval_k` and `chunks_per_sub_question` stay at 8.** Raising `k`
  alone changes nothing (the fan-in cap cuts the extra chunks back out), and
  raising both costs ~2.5x synthesis context to buy figures C1 now delivers
  exactly at no context cost.

### Open

1. ~~**Stage E.**~~ Done 2026-08-01, 30/30 — see "Stage E" above.
2. **`citation_accuracy` fell to 0.370 and was recovered to 0.741**, above the
   0.623 baseline on those questions, by two rendering fixes (ADR 0022
   formatting, ADR 0024 cite-every-figure). The drop was never mainly a
   citation defect: the *claims* changed, and the plain claims that scored
   0.857 disappeared. It is **not in `TARGETS`** and never gated. The residual
   6 of 23 — claims naming a document location, or a different filing than the
   citation — needs synthesis constrained, which stays the operator's call.
3. **The recall threshold is 0.65 and the full set now measures 1.000.** It
   was set from a subset, which gives a floor and not a ceiling; the full set
   has since scored 28 of 28, so the gate has 0.35 of slack and no longer
   costs a declining system anything. Raising it is now a decision with a
   measurement behind it — but one reading is one reading, and this metric
   swung ~0.14 between runs, so pick the new floor from more than this run.
4. **`main` is 49 commits behind `dev`** (33 when this plan was written).
   Phase 4 §5's CI work assumes a sane default branch; merge first.
5. **A node abort after synthesis bypasses retraction — a product defect, not
   an eval one.** `guarded` converts a timeout into `error` on the state and
   the graph routes to END, so `retract` never runs. Whatever the last
   synthesize produced is returned as the answer with its unsupported claims
   intact. On 2026-07-31 g-010 came back as a 1158-character draft carrying 8
   unsupported claims; `fie ask` would have shown a user exactly that. This is
   the same class of surviving-hallucinated-text that ADR 0021 closed for
   excision failures, reached by a different route, and it means "retraction
   is load-bearing" holds only on the paths that reach `retract`.

   The eval side is now covered — `_run_gold` retries a question whose
   `RunResult.error` is set, so a stall costs an attempt instead of the
   baseline, and `degraded_attempts` keeps the infra cost visible in the
   report. That is deliberately **not** a fix for this: it stops the abort
   reaching the metrics, not the user.

   Measured for whoever picks this up: a synthesize call stalls ~1.5% of the
   time at the 600s cap (3 in 198 calls across the three 600s-era 30-Q runs;
   8 in 223 at the old 300s cap). Stalls land *exactly* on the cap while clean
   calls run 21s median, 206s max — bimodal, so these are hangs, not slow
   work. Every run so far has had GLM on synthesis, so whether the model
   causes it is untested, not disproven.

   Options, none chosen: route an errored post-synthesis state through
   `retract` before END; or have `build_run_result` refuse to publish prose
   whose claims were never verified, and return the error instead. The second
   is closer to the blueprint's contract — an unverified answer is not an
   answer — but it changes what a partial result means, so it needs an ADR.
