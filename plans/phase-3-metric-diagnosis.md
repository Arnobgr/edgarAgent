# Phase 3 — Diagnosing the three failing gate metrics

**Status:** re-baseline ran 30/30 and missed 2 of 3 targets; both misses
root-caused and one fixed (ADR 0017) — **awaiting a second re-baseline** ·
**Created:** 2026-07-26 · **Updated:** 2026-07-27 · **Blocks:** Phase 4

## ▶ NEXT SESSION STARTS HERE — Task 5: close `grounding_rate`, then re-run

**Do the free diagnosis (step 1) before spending on the run (step 2).** On
current evidence a run today clears `numeric_fidelity` but still misses
`grounding_rate` by a claim or two, i.e. burns 75 minutes and real money to
refuse a baseline again.

### ✔ Step 1 OUTCOME (2026-07-28) — diagnosed and fixed, ADR 0018

First correction: the report's 108/121 is the *reduced fraction* — the real
population is **242 non-retracted claims, 216 grounded, 26 failures**
(`citation_judged` 216 confirms it), so the gate needs **+2** grounded
claims (218/242 = 0.9008), and the failure shape is 16 numeric-partial /
6 qualitative-partial / 4 numeric-unsupported.

The leading hypothesis was wrong in a useful way: the mapper *does* emit
ratio operands. The real blocks, each verified by store probe or live mapper
call: (1) growth percents need `(a−b)/b` across two fiscal years — the ratio
path computes `num/den` inside one year; (2) the mapper's camel-case terms
(`NetSales`) can't reach spaced labels ("Net sales") through `LIKE`; (3) the
cited-text fallback still demanded day-of-month fragments ("September 27" →
27). All fixed reconciler-side (ADR 0018); g-012's 19.0% margin and g-011's
133% segment growth are **correct rulings** and stay unsupported.

Replay of the 26 failures through the fixed reconciler: **11/20 numeric
failures now ground** → projected ~227/242 ≈ 0.94. Verifier P/R re-measured
after the change: **1.000/1.000 on all 112 labeled claims**. `make check`
green (149 tests). Step 2 is go.

### Where things stand

The 2026-07-27 re-baseline (`eval/reports/agent-20260727-224458.json`) was
**the first structurally clean run**: 30/30 questions, no drops, no budget
aborts, `citation_accuracy` 0.731 (216 pairs, 0 judge failures). Working tree
is green (`make check`, 142 tests) at commit `fdcb37f`.

| metric | target | measured | status |
|---|---|---|---|
| n_completed | 30 | **30** | ✅ (previous run: 29) |
| hallucination_rate | ≤ 0.02 | **0.017** | ✅ first time passing |
| verifier P/R (all) | — | **1.000** | ✅ |
| numeric_fidelity | ≥ 0.85 | **0.654** (53/81) | **fixed** → re-scores **0.877** ✓ |
| grounding_rate | ≥ 0.90 | **0.893** (108/121) | ✗ **this task** |
| citation_accuracy | (info) | 0.731 | stable vs 0.728 |
| expected_fact_coverage | (info) | 0.250 (7/28) | ✗ undiagnosed, not a gate |

`numeric_fidelity` is **already fixed** (ADR 0017): `parse_numbers` read
"$391 billion" but not "$298,085M", so 18 of 28 misses were figures compared
10^6 too small. Re-scored on the same answers with the fixed parser: 0.877.
The 10 residual misses are the honest ceiling (correct figures with no
ingested XBRL counterpart, per-share repurchase prices) — **zero
fabrications**. Nothing further to do there; do not tune retrieval or
synthesis for it.

### Step 1 — classify the 13 non-grounded claims (free, no API spend)

`grounding_rate` 0.893 needs **one more grounded claim out of 121**. Known
shape of the 13 failures:

| verdict | n | note |
|---|---|---|
| numeric / `partial` | 8 | the target population — mostly derived ratios |
| qualitative / `partial` | 3 | check whether the verifier is right before "fixing" |
| numeric / `unsupported` | 2 | these two *are* the `hallucination_rate` numerator |

**Leading hypothesis:** ADR 0016 added a ratio-grounding path (recompute a
percent from store facts named by the mapper) but the `partial`-heavy shape
says it is not firing on these claims. Four known examples, all g-013 — note
the feedback names the *percent* as the missing figure while the dollar
operands reconcile:

```
numeric/partial: "Products revenue declined approximately 5.7% in FY2023
  compared to FY2022, derived from the absolute figures of $298,085M and
  $316,199M."
  feedback: figure(s) 5.7% not found in the XBRL facts store — searched XBRL
  concepts: ProductsRevenue, decline, percentage and 1 cited table chunk(s).
```

Start by checking whether the mapper emits usable ratio operands for these,
and whether the recomputation path in `src/fie/tools/reconcile.py` is reached
at all (a log line or a targeted offline test on the claim text above is
enough — the ratio path itself is deterministic once the mapping exists).

**Recipe — recovering claims, verdicts and answers from checkpoints.** Run
ids are in the report's `run_ids`; `thread_id` is the run id. Only claims
whose `status != "retracted"` are in the `grounding` population that the
metric scores, which is what makes the ratio come out at exactly 108/121:

```python
import json, sqlite3, warnings
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

warnings.filterwarnings("ignore")  # unregistered-type msgpack noise
report = json.load(open("eval/reports/agent-20260727-224458.json"))
serde, conn = JsonPlusSerializer(), sqlite3.connect("data/checkpoints.db")

for qid, run_id in report["run_ids"].items():
    for typ, blob in conn.execute(
        "select type, checkpoint from checkpoints where thread_id = ?"
        " order by checkpoint_id desc", (run_id,)
    ):
        vals = serde.loads_typed((typ, blob)).get("channel_values", {})
        claims, verdicts = vals.get("claims"), vals.get("verdicts")
        if claims and verdicts:                    # newest complete checkpoint
            vmap = {v.claim_id: v for v in verdicts}
            for c in claims:
                if c.status != "retracted" and (v := vmap.get(c.id)):
                    print(qid, c.claim_type, v.verdict, c.text, v.feedback)
            break                                  # `draft_answer` is here too
```

**Scope guards.** Retrieval (ADR 0011), synthesis prompts and model selection
are all settled and are *not* how this metric gets closed — the same
conclusion Tasks 1–4 reached. Any reconciler change re-opens verifier
behavior, so re-measure verifier P/R on the 112 labeled claims before
trusting the result (ADR 0016's standing rule); it runs automatically inside
`eval-agent` as `verifier_precision_*`. If the 13 turn out to be *correct*
rulings, say so and raise the question of whether 0.90 is the right target
rather than tuning toward it.

### Step 2 — the second re-baseline

Only after step 1 lands. **Smoke first** — `uv run eval-agent --limit 2
--skip-judge` (~5 min, cents) caught a run-killing bug before the last paid
run and would have cost 75 wasted minutes otherwise. Then the full run per
"The run" below. `eval/baselines.json` is still all-`null` on purpose; the
guard refuses to write it unless the run hits every target with 30/30.

### Deferred, deliberately

`expected_fact_coverage` 0.250 (7/28) is informational and undiagnosed. It is
**not** explained by the ADR 0017 parser defect — re-scoring the same answers
with scales normalized leaves it at 7/28 exactly. Its denominator also became
honest only recently (ADR 0015), so it is not comparable to the older 0.400.

## The original re-baseline instructions (still current)

Everything below this section is history; nothing else needs re-deriving.
All diagnosis and fixes are committed through `9541b8d` (metric re-scope
ADR 0015; reconciler fixes + harness hardening ADR 0016; verifier P/R
re-measured at **1.000/1.000** on the 112 labeled claims, up from 0.982).
The command and harness behavior below are unchanged for the second run.

### ✔ Decision made: numeric_fidelity target is 0.85

Applied to `TARGETS` in `src/fie/eval/agent_runner.py`; rationale recorded in
ADR 0015 § Consequences. The §13 draft's 0.95 sat above the re-scoped
metric's honest ceiling (~0.9) — derived and correct-but-unfiled figures miss
by design — and since `--write-baseline` now gates on `TARGETS`, it would
have refused every baseline regardless of the run. The metric is a
fabrication tripwire (chance floor ~0.5), not a quality ladder; quality is
`grounding_rate`, still 0.90.

### The run

```bash
make qdrant-up          # Qdrant only is enough; keys in .env
uv run eval-agent --write-baseline    # ~75 min, real money
```

New harness behavior since the failed run: the report always lands in
`eval/reports/`, per-pair citation judgments are persisted (no paid
re-judge needed for diagnosis), a dropped question exits non-zero and
refuses the baseline, and `--write-baseline` refuses a run that misses the
targets unless `--force`. So a single run either writes the Phase 4
baseline or fails loudly with everything needed to diagnose.

**Smoke first — it paid for itself on 2026-07-27.** A `--limit 2
--skip-judge` check before the paid run dropped g-001 with `'NoneType'
object has no attribute 'passed'`: `with_structured_output(
method="function_calling")` returns `None` rather than raising when the model
answers in prose instead of calling the tool, so `ainvoke_with_retry` never
retried and the `None` reached the caller's `cast`. Both GLM roles
(`synthesis`, `critic`) are on that path, so the full run would have lost
questions and refused the baseline 75 minutes in. Fixed — an empty
structured output is now a retryable failure (`EmptyStructuredOutputError`).

### What to expect (projections from the recovered baseline answers)

| metric | target | last measured | expected now |
|---|---|---|---|
| grounding_rate | ≥ 0.90 | 0.735 | **0.87–0.94** (reconciler fixes; upper half needs the mapper to use its new YTD/ratio vocabulary) |
| numeric_fidelity (re-scoped) | 0.85 (set) | 0.878 equiv. | **~0.88** |
| hallucination_rate | ≤ 0.02 | 0.030 | ~0.02 — was 6 claims; several were mechanical `unsupported`s the fixes may ground |
| expected_fact_coverage | (info) | 0.400 | up — denominator fixed, labels resolvable |
| n_completed | = 30 | 29 | **must be 30** (guard enforces) |

### If it fails

Diagnose **free** from `data/checkpoints.db` — answers, claims, verdicts
and feedback are all recoverable per run_id (recipe: the extraction scripts
described under "grounding_rate diagnosis" below; run ids are in the
report). Classify residual non-grounded claims by the feedback field before
touching anything. Retrieval/synthesis/model changes remain out of scope
unless genuinely-wrong figures dominate, which no evidence so far supports.

### Before Phase 4 (`plans/phase-4-backend-and-deploy.md`), also finalize

1. ~~**`make typecheck` is red on clean dev**~~ — **fixed 2026-07-27.** The 25
   errors were not stub drift: without `plugins = ["pydantic.mypy"]`, mypy
   synthesises pydantic `__init__`s via `dataclass_transform`, which keys
   parameters by field *alias* and ignores `populate_by_name` — so
   `ChatAnthropic(model=...)` (field `model`, alias `model_name`) read as an
   unexpected keyword. Enabling the plugin cleared 22 of 25; the rest were a
   bare `Runnable` return and a `dict[str, Literal[...]]`/`dict[str, str]`
   invariance clash on the role metadata. **`make check` is green**, so
   Phase 4 CI can run it from day one.
2. ~~**`unanswerable_declined` counts an errored empty answer as a successful
   decline**~~ — **fixed 2026-07-27.** An errored or empty result now scores
   as a miss, not a decline (`agent_runner.py`, tests in
   `tests/test_agent_runner.py`). Affects the 3 of 30 runs that aborted.
3. Accepted deferrals, recorded so they aren't re-litigated: fact-reference
   rendering (revisit at Phase 5 as a product feature), store ingest gaps
   (`period_end` NULL for instants, dimensions collapsed to a flag),
   citation-span structure (fact spans don't name the company; absence
   claims unsupportable by any span — citation_accuracy is not a gate),
   g-010's SQLITE_MISUSE root cause (unreproduced; detection guard landed).

## ✔ OUTCOME (2026-07-27) — the diagnosis this plan produced

All four tasks ran against the baseline artifacts (answers recovered from
`data/checkpoints.db` — no paid re-run). Full decision record: ADR 0015.

- **Task 1 (numeric_fidelity 0.566): the metric was 94% noise.** Of 1,352
  numbers scored, 1,085 (80%) were digit runs inside bracketed citation ids,
  48 were in the retracted-claims section, 136 were dates/ordinals/form
  names/product names ("10-K" → 10, "H200" → 200). Only 83 were financial
  figures; those matched at 0.88, ~90% against the *right* fact. All 10
  genuine misses were derived arithmetic (bucket A) or correct figures with
  no XBRL counterpart; **zero bucket B or C**. The metric is now scoped to
  currency/scale figures with citations and the retracted section stripped
  (`metrics.py`); it reads **0.878** on the same answers. Retrieval and
  synthesis were never the problem — do not tune them for this metric.
  **Open gate decision:** the honest ceiling of the scoped metric on real
  answers is ~0.9 (derived + non-filed figures score as misses by design),
  so the §13 target of 0.95 needs re-setting — or the fact-reference
  rendering alternative below, which remains unimplemented and optional.
- **Task 2 (citation_accuracy 0.728):** a 30-pair hand-check found nearly all
  spans genuinely support their claims. The rejectable ones are structural:
  absence claims ("evidence does not contain X" — unsupportable by any
  span), XBRL fact spans that never name the company, mangled table-fallback
  fragments, and truncated spans. Per-pair judgments were not persisted and
  claim ids were ambiguous across questions, so *which* 40 pairs failed is
  unrecoverable — both fixed for future runs (judgments persisted under
  run-qualified ids; judge API failures no longer scored as bad citations).
- **Task 3 (expected_fact_coverage 0.400): root cause found.** Instant
  (balance-sheet) facts are stored with `fiscal_period = NULL`; the five
  dropped labels said `"FY"`, which `query_facts` matches exactly —
  unsatisfiable by construction. Labels fixed (fiscal_period removed), the
  metric now counts unresolvable facts and missing results as misses, and
  matching accepts any resolved candidate balance. All gold labels resolve
  again (0 unresolved).
- **Task 4:** the per-question `IngestStore` leak is fixed
  (`reconciler.close()` in `runner.py` and the verifier eval); `eval-agent`
  now exits non-zero on `n_completed < n_questions`; `--write-baseline`
  refuses target-missing or incomplete runs without `--force` and records
  coverage. **New evidence on g-010:** its checkpoint contains a complete,
  fully verified answer (8 claims, all grounded), so the SQLITE_MISUSE hit
  the post-graph tail of `answer_question`, not startup — the "died at
  startup" account above is wrong. Cause still unreproduced; the guard makes
  any recurrence loud instead of silent.
- **Also observed:** 3 of 30 runs (g-011, g-018, g-021) ended with `error`
  set and empty answers — they sit in every denominator as zero-claim
  results, and an errored empty answer currently counts as a successful
  decline in `unanswerable_declined`. Not yet addressed.

**Next steps:** superseded — see "▶ NEXT SESSION STARTS HERE" at the top.
(Of the decisions this paragraph once listed: fact-reference rendering was
evaluated and deferred to Phase 5; the grounding_rate task was promoted,
diagnosed and fixed the same day — sections below.)

## ✔ grounding_rate diagnosis (2026-07-27, same recovered states)

Of 200 grounding rows, 53 fail; 47 of those are `partial` verdicts from
numeric reconciliation. Decomposition, with the store cross-checked per miss:

- **18 — parser noise in claim text.** The reconciler runs `parse_numbers`
  over the claim and demands every token reconcile: "September 27, 2025"
  → `27`, "Q1" → `1`, "more than 12 months" → `12`, "10-K" → `10`. All
  dollar figures in these claims matched; the date/ordinal fragments
  cannot, so the claim lands `partial`. Same defect family as the
  numeric_fidelity noise (ADR 0015), now in the *runtime verifier*.
- **14 — percent figures.** Margins and growth rates ("18.7%", "65%") are
  derived ratios, never filed facts. Both operands (GrossProfit, Revenues)
  are in the store — a deterministic recomputation path would ground them.
- **~7 — YTD period vocabulary gap.** Nine/six-month figures exist in the
  store as `YTD9`/`YTD6`, but `FactCandidate.fiscal_period` tells the
  mapper "'FY' or 'Q1'..'Q4'" — the store's YTD labels are unreachable by
  schema. E.g. "$69,926M nine months ended 2025-09-30" is exactly
  `TSLA us-gaap:Revenues FY2025YTD9`.
- **3 — instant NULL-period mismatch** (Task 3's root cause at runtime):
  g-019's total-assets claims; facts exist with `fiscal_period = NULL`,
  the mapper emits "FY".
- **~3 — genuinely non-XBRL or absent facts** (press-release SBC
  breakdown, AAPL services revenue — the store lacks it).
- **~3 — legitimately derived** ("implied $18.6B", "decreased 2.8%") —
  correct `partial`/`unsupported` rulings.
- **5 — qualitative**, mostly the verifier catching real claim/source
  discrepancies (e.g. "decreases in regulatory credits revenue" vs the
  source's "increases"). The system working.

Ceiling math: fixing noise + YTD + instants ⇒ ~0.875; adding percent
recomputation ⇒ ~0.945. **The 0.90 target is reachable with reconciler-only
changes** — no retrieval, synthesis or model work. Side effect worth having:
each bogus `partial` currently fires a verify→research loop-back, so the
noise also burns budget every run (three runs ended in budget/error aborts
with empty answers).

Proposed fixes, in order: (1) reconcile only financial figures from claim
text (currency/scale-worded, plus percents once (2) exists); (2) ground
percent claims by recomputing ratio candidates from store facts named by the
mapping; (3) add YTD6/YTD9 to the mapper's period vocabulary and retry the
lookup without `fiscal_period` before falling back to tables; (4) treat
"as of <date>" claims as instants: query with `fiscal_period=None`. Each
changes verifier behavior, so re-run `eval-retrieval`-style verifier P/R on
the labeled claims before trusting them (numeric arm needs the mapper model —
paid, small).

**Implemented 2026-07-27 — ADR 0016.** All four fixes landed in
`tools/reconcile.py` (fixes 3+4 share one mechanism: a period-free retry
that never drops the fiscal year, preserving `test_period_shift_is_caught`).
Claims with no financial figures now verify against cited text via the
interim reconciler. Offline tests cover each failure class.

**Verifier P/R re-run same day: 1.000/1.000 on all 112 labeled claims**
(was 0.982/0.982). The run surfaced and fixed two further defects the
aggregate had hidden: period-omitted mappings matching partial-year values
(now `_period_admissible`, safe-by-default) and sign-sensitivity against
the sign-blind parser (now magnitude comparison throughout). Details and
caveats in ADR 0016. Remaining spend decision: the 30-Q re-baseline.

---

## ▶ START HERE (original plan, superseded by the outcome above)

The prerequisite is **satisfied**: the 30-Q baseline ran 2026-07-27 and the
exit gate FAILED on all three targets. Everything below consumes that report.

```bash
ls eval/reports/agent-20260727-093137.json   # the raw material; plus data/ingest.db
```

**Do Task 1 first. Tasks 1 and 2 cost nothing in API spend** — both are scripts
and hand-checks over an artifact you already paid for. Exhaust them before
spending another ~75 min and real money on a re-run.

**State you can rely on, so nothing needs re-deriving:**

- Corpus ingested, both Qdrant collections at 4,181 points. Config is the GLM
  arm (`synthesis`/`critic` → `z-ai/glm-5.2-20260616`, `eval_judge` →
  `claude-sonnet-5`). `uv run pytest -q` → 118 passed. Langfuse on port 3001.
- All 80 eval labels are `verified`. ADR 0011 (retrieval) is settled on them.
- `eval/baselines.json` is all-`null` **on purpose** — the failing run's
  baseline was reverted by hand. Do not re-run `--write-baseline` until the
  metric definitions here are settled; see the `--write-baseline` footgun in
  Task 4.
- Model selection is **settled** (GLM, on cost). Re-opening it is not how these
  metrics get fixed.

### The gate, as measured (n=29 questions, 147 claims judged)

| metric | target | measured | gap |
|---|---|---|---|
| grounding_rate | ≥ 0.90 | **0.735** | large — no known spec problem |
| **numeric_fidelity** | **≥ 0.95** | **0.566** | **enormous — spec suspect** |
| hallucination_rate | ≤ 0.02 | **0.030** | closest to target |
| citation_accuracy | (not a gate) | 0.728 | Task 2 |
| expected_fact_coverage | (not a gate) | 0.400 | denominator bug, Task 4 |

**Every n=5 figure in the git history of this plan is superseded.** Grounding
read 0.833 on 15 claims and 0.735 on 147; citation accuracy read 0.381–0.667 on
15–21 claims and 0.728 on 147. Treat the small-sample numbers as noise
wherever they still appear below.

`numeric_fidelity` remains the one that cannot be closed by tuning — but note
the load-bearing evidence for that is the **measured chance floor** below, not
the cross-model stability argument. Stability across models (0.50–0.71) was
measured at n=5, where grounding moved 0.10 on the same model, so it is weaker
than it looks. The chance floor stands on its own.

**Verification is not the problem.** Verifier precision/recall is ~0.98
(numeric 0.979, qualitative 1.000), so failing grounding and numeric_fidelity
come from synthesis or from the metric definitions — not from the verifier.

## The governing lesson

The retraction bug (fixed 2026-07-26, see `phase-3-exit-gate.md`) was a metric
that routed *around* the failure it was supposed to catch: unsupported claims
that survived in the prose were filed as `retracted` and excluded from
`hallucination_rate`, which therefore read a perfect `0.000`. **A gate metric
reading implausibly good, or implausibly bad, is itself evidence worth
checking before changing the system.** Both tasks below apply that lens.

---

## Task 1 — Decompose the `numeric_fidelity` misses (do this first)

**Do not "improve retrieval" to chase this number until this task is done.**

### What the metric actually does

`numeric_fidelity` (`src/fie/eval/metrics.py:123-142`) does **not** involve the
`fact_mapping` role, the reconciler, or retrieval. It is a pure text-vs-store
check:

1. `parse_numbers(result.answer)` (`src/fie/tools/reconcile.py:88`) extracts
   every number from the **final answer prose** — not from claims.
2. Percent figures are skipped. 4-digit years are skipped.
3. Each remaining number is matched against `fact_values` — **the entire XBRL
   store's value column** — within a tolerance derived from printed precision
   (`0.5 * 10^-decimals * scale`).
4. Score = matched / total.

Two consequences that matter enormously:

- **The match ignores company, concept, and period.** It only asks "does *some*
  fact anywhere in the store have this value?" It is a spellcheck against a
  dictionary of numbers, not a fact check.
- **Derived arithmetic can never match.** "Revenue increased by $917 million",
  a sum of two segments, an average, a per-vehicle figure — none of these are
  filed XBRL facts. They are scored as failures **by construction**, no matter
  how correct they are.

### Measured chance floor (2026-07-26) — read this before interpreting 0.566

The store holds 25,937 facts but only **7,428 distinct values**. Feeding the
matcher numbers invented at random:

| probe | match rate |
|---|---|
| random `$X.Y billion` (one decimal, tolerance ±$50M) | **236/500 = 47%** |
| random bare integers 1–100 (tolerance ±0.5) | **52/100 = 52%** |
| random `$X.YZ billion` (two decimals, tolerance ±$5M) | 77/500 = 15% |

**A number invented from thin air passes ~47% of the time** at the precision
financial writing normally uses, because a ±$50M window usually contains one
of 7,428 scattered values by luck.

Our measured `numeric_fidelity` is **0.566** (n=29, 2026-07-27) against a
chance floor of ~0.47.
**The score is barely above what random numbers would earn**, so it has very
little discriminating power and is close to uninformative in *both*
directions. This is why it sits near 0.5 no matter which model writes the
answer — that stability is a property of the metric, not of the models.

> An earlier draft of this plan inferred from the permissiveness that "the
> misses are more likely derived figures than wrong lookups." **That inference
> was not justified** and has been removed. The leniency cuts both ways: it
> inflates passes as much as the derived-figure problem depresses them. The
> composition of both populations is unmeasured — that is what Task 1 is for.

### The work — sample BOTH failures and passes

Because of the chance floor, classifying only the misses answers half the
question. Take the answers from the completed 30-Q baseline run and sample
**40–60 failing numbers and 40–60 passing numbers**.

Failing numbers:

| bucket | meaning | correct response |
|---|---|---|
| **A. derived** | legitimate arithmetic (difference, sum, ratio, average, per-unit) | metric bug — scope the metric |
| **B. scale/unit** | right fact, wrong magnitude ("$97.7B" vs a store value in thousands) | real bug in synthesis or `parse_numbers` scaling |
| **C. genuinely wrong** | no such figure exists | real bug — this is what the metric is *for* |
| **D. stranded text** | number sits in text left behind by a failed excision | already fixed 2026-07-26; should be ~0 in the new run |

Passing numbers — for each, check whether the fact it matched is actually the
*right* one (same ticker, concept, and period as the claim):

| bucket | meaning | correct response |
|---|---|---|
| **P1. true pass** | matched the fact the sentence is actually about | genuine signal |
| **P2. coincidental pass** | matched an unrelated fact (wrong company, concept, or year) | the metric is crediting luck |

The P1/P2 split is the number that tells you whether `numeric_fidelity` is
worth keeping at all. If P2 is large, the metric is closer to noise than to
measurement, and no amount of system work will make it mean something.

Write the classifier as a script under `scratchpad/`; it needs
`eval/reports/agent-<ts>.json` plus `data/ingest.db`. Report both splits.

### Decision rule

- **P2 large (coincidental passes dominate)** → replace the metric rather than
  tune it. See "The structural alternative" below. This outranks the A/B/C
  split: a metric that credits luck cannot be repaired by reclassifying its
  failures.
- **Mostly bucket A** → the metric is mis-specified. Restrict it to figures the
  answer presents as filed values, or score derived figures against a
  recomputation. Then re-baseline. Do not touch retrieval.
- **Mostly B** → fix scale handling; check `_context_scale` and the "(In
  millions)" table convention in the corpus.
- **Mostly C** → this is the real thing. *Now* retrieval/synthesis work is
  justified, and only now.

**Do not raise the tolerance to make the number go up.** That converts a weak
metric into a meaningless one — the same failure mode as the retraction bug.
Note the tolerance already runs the wrong way: *more* printed precision means a
*tighter* window (two decimals drops chance matching 47% → 15%), so loosening
tolerance directly buys more coincidental passes.

---

## The structural alternative — render numbers, don't generate them

Worth evaluating **before** investing in repairing the metric, because it may
be cheaper than tuning a generate-then-check pipeline to a 0.95 target.

Today the model writes numbers into prose and a reconciler checks them
afterwards: **detect-after-the-fact**. The standard production pattern for
numeric agentic work (program-aided generation / tool-computed values /
semantic layers) inverts this: **the model chooses which number, code produces
the number — the model never types a digit.**

Applied here:

1. **Fact references instead of literals.** Synthesis emits
   `{{fact:TSLA:Revenues:FY2025}}`; a deterministic renderer resolves it
   against `ingest.db` and formats it. An unresolvable placeholder is a hard
   error, not a silently wrong number.
2. **Derived values computed in Python.** `{{delta:...}}` is `a - b` in code,
   so "an increase of $917 million" is correct by construction and its
   operands are inspectable.
3. **Narrative figures stay quote-anchored.** Numbers that exist only in 10-K
   prose get a verbatim span citation instead of a fact ref.

**Most of the machinery already exists.** `IngestStore.query_facts()` takes
`(tickers, concept_terms, fiscal_year, fiscal_period, unit)` and returns typed
`Fact` rows; the `fact_mapping` role already produces exactly those query
parameters. The change is largely moving that step from *after* generation
(verification) to *before* rendering — a rewiring plus a renderer, not a new
subsystem.

**What it does to evaluation:** "is this number right?" stops being an
unbounded question (any number could appear, so you need a checker — and this
plan documents how badly that checker performs) and becomes a bounded
selection question: *did it pick the right fact?* Rendering fidelity becomes
1.0 by construction — an invariant to assert in tests, not a metric — and the
real metric becomes fact-selection accuracy, which `gold_set.jsonl`'s
`expected_facts` already encodes ground truth for.

**Honest limits:** it does not fix retrieval (confidently picking the *wrong*
fact is still possible — it converts hallucination into misselection, a
smaller and more measurable error class); prose fluency suffers around
placeholders and needs a lint that rejects literal digits outside them; and
figures with no XBRL counterpart need the fallback in (3). It is an
architecture change and warrants an ADR.

**Sequencing:** do Task 1 first regardless. It costs about an hour and tells
you whether you are buying a rewrite or a bug fix. Note that even a perfect
fact-rendering system still scores below 0.95 under the *current* metric
definition, because derived figures remain unmatched — so the metric needs
scoping either way.

---

## Task 2 — `citation_accuracy`: why do cited spans not support their claims?

`citation_accuracy` (`metrics.py:114-120`) is the fraction of judged
`(claim, span)` pairs the `eval_judge` rules as supported.

**Measured 2026-07-27: 0.728 over 147 judged claims.** This supersedes the n=5
readings this task was originally written around (GLM 0.667 / 15 claims, Opus
0.381 / 21 claims) — **the problem is real but far milder than the 0.381
suggested**, and citation_accuracy is not a gate metric. Size the effort
accordingly and do Task 1 first.

This is a **different failure** from `numeric_fidelity` and should not be
lumped together with it. The tension still worth explaining: verifier
precision/recall is ~0.98, so the verifier judges claims against their sources
accurately — yet the judge rules ~27% of cited spans unsupported. Both cannot
be a simple accuracy story; one of them is measuring something other than what
its name suggests.

Candidate causes, cheapest to check first:

1. **Wrong chunk id cited.** Synthesis emits `source_chunk_ids` that don't
   correspond to the sentence it wrote. Check by sampling claims and diffing
   the cited chunk text against the claim.
2. **Span extraction, not citation.** The judge is handed a *span* — check how
   that span is selected in `eval/agent_runner.py:52-58`. If the span is a
   truncation or the wrong slice of a correct chunk, the citation is fine and
   the harness is wrong.
3. **Judge strictness.** `eval_judge` may demand verbatim support where the
   claim is a fair paraphrase. The report emits
   `citation_spot_check_sample` for exactly this — hand-check those ~20% per
   blueprint §6 before trusting the number.

**Start with (3)**, because it is free: the spot-check sample is already in
every report. If the judge is being unreasonable, the metric is wrong and
tasks (1) and (2) are wasted effort.

### Confound to control

Opus 5 scored roughly half GLM's citation accuracy while emitting 40% more
claims (21 vs 15). Per-claim metrics penalise models that decompose harder,
and Opus 5 is documented as producing longer output by default without an
explicit conciseness instruction (which our synthesis prompt lacks). Do not
read the Opus/GLM citation gap as a quality ranking — n=5, and the claim
counts differ.

---

## Task 3 — `expected_fact_coverage` drops labels instead of counting misses

**Small, self-contained, and independent of Tasks 1–2.** Measured 0.400.

Expected facts that fail to resolve in the store hit `continue` **before**
`total += 1` (`agent_runner.py:114-117`), so the fact leaves the denominator
entirely rather than scoring as a miss. The metric is silently computed over a
subset of the labels you verified — the same class of defect as the retraction
bug in "The governing lesson": a metric routing *around* the failure it exists
to catch.

Five facts were dropped in the 2026-07-27 run (all logged as
`no fact in store`):

| question | fact |
|---|---|
| g-006 | NVDA FY2026 `us-gaap:Assets` / "total assets" |
| g-008 | AAPL FY2025 `CashAndCashEquivalents` |
| g-019 | TSLA FY2025 `us-gaap:Assets` |
| g-019 | AAPL FY2025 `us-gaap:Assets` |
| g-019 | NVDA FY2026 `us-gaap:Assets` |

**These labels are `verified`, so failing to resolve is itself the finding.**
Diagnose before changing the metric: `us-gaap:Assets` failing for all three
tickers points at `query_facts` concept-term matching, not at missing data —
25,937 facts are in the store and total assets is about as standard as a
concept gets. Check whether `concept_terms` needs the `us-gaap:` prefix
stripped, and whether `fiscal_year` for NVDA FY2026 matches how the store
records it.

Then decide the metric's contract: an unresolvable *expected* fact is either a
label bug (fix the label) or a store/query bug (fix the query) — but it should
never silently shrink the denominator. Make it count as a miss or raise.

## Task 4 — Reproducibility and safety fixes before the next paid run

**Batch these before any re-baseline**, so the one expensive re-run the plan
anticipates is clean. None of them changes a metric value.

1. **SQLite `SQLITE_MISUSE` loses questions.** The 2026-07-27 run completed
   **29 of 30** — g-010 died at startup with `bad parameter or other API
   misuse` and was logged-and-dropped (`agent_runner.py:163-164`), silently
   shrinking every metric's denominator. Two candidate defects, cause
   **unconfirmed** (one occurrence, timing-dependent, not reproduced):
   - `runner.py:94` builds an `IngestStore` per question via
     `build_production_reconciler` and **never closes it**. Connections leak
     across the run, each `check_same_thread=False` and used from worker
     threads via `asyncio.to_thread`.
   - All questions share one `checkpoints.db` through `AsyncSqliteSaver`
     (`runner.py:105`); a node timeout or cancellation mid-checkpoint-write can
     leave that connection broken. **Sequencing favours this one** — g-009 hit
     a DNS failure (`[Errno 8] nodename nor servname provided`), then g-010
     failed instantly.

   Reproduce before fixing. A regression test wants to assert `n_completed ==
   n_questions`, since the drop is what makes the metrics quietly wrong.

2. **`--write-baseline` is a footgun.** It overwrites `eval/baselines.json`
   unconditionally (`agent_runner.py:267-281`) — no gate check — and does not
   carry `n_questions`/`n_completed` across, so a 29-question failing run
   writes a file that reads like a clean 30-question accepted baseline. It did
   exactly that on 2026-07-27 and was reverted by hand. Gate it on the
   absolute §13 targets or require an explicit `--force`, and record coverage
   in the file.

3. **Do not re-litigate the GLM-vs-Sonnet exception counts.** The "10
   `OutputParserException`s on Sonnet vs 0 on GLM" confounder is **resolved and
   dead as evidence**: the 2026-07-27 GLM run threw the same failure (Pydantic
   `ValidationError` — `SynthesisOutput.claims[25]` returned `{}`, missing
   `text` and `claim_type`) and recovered via `ainvoke_with_retry`
   (`guardrails/execution.py:48`), which retries 3× and logs each attempt with
   `exc_info`. These are logged-then-recovered retries and **both arms throw
   them**. The counts measure logging, not quality.

## What is explicitly out of scope here

- **Model selection.** Settled: stay on GLM 5.2 on cost. See the head-to-head
  in `phase-3-exit-gate.md`. Re-opening it is not how these metrics get fixed.
- **Retrieval parameters.** ADR 0011 is settled on verified labels
  (`openai` + no rerank). Do not re-tune retrieval until Task 1 shows bucket C
  dominates — otherwise you are tuning against a mis-specified metric.
- ~~**`grounding_rate` 0.833 → 0.90.** The smallest of the three gaps and the
  most likely to move once the other two are understood. Leave it last.~~
  **Revised 2026-07-27 — this no longer holds.** It was justified by 0.833
  being the smallest gap; the real n=29 figure is **0.735**, a 0.165 gap. Since
  `grounding_rate` has no known specification problem (unlike numeric_fidelity)
  and the verifier behind it runs at ~0.98 P/R, it is arguably the most
  *actionable* of the three. It is still sequenced after Task 1 — but promote
  it to a real task once Task 1 reports, rather than leaving it last by
  default. Note the per-claim denominator caveat under "Confound to control":
  a model that decomposes harder is mechanically penalised, so check
  claims-per-question alongside any grounding change.
