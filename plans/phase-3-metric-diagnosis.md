# Phase 3 — Diagnosing the two failing gate metrics

**Status:** not started · **Created:** 2026-07-26 · **Blocks:** Phase 4

Read this **before** attempting to fix retrieval, prompts, or models to close
the exit gate. Two of the three gate metrics are far from target:

| metric | measured (best arm) | target | gap |
|---|---|---|---|
| grounding_rate | 0.833 | ≥ 0.90 | close |
| **numeric_fidelity** | **~0.53** | **≥ 0.95** | **enormous** |
| hallucination_rate | unreliable until re-measured | ≤ 2% | unknown |

`numeric_fidelity` is the one that cannot be closed by tuning. A gap that
large, that stable across every model tried (Sonnet, GLM 5.2, Grok 4.5,
Opus 5 — all 0.50–0.71), is a signal about **how it is measured**, not about
which model writes the answer.

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
  fact anywhere in the store have this value?" This is a very permissive check.
- **Derived arithmetic can never match.** "Revenue increased by $917 million",
  a sum of two segments, an average, a per-vehicle figure — none of these are
  filed XBRL facts. They are scored as failures **by construction**, no matter
  how correct they are.

A check this permissive still failing ~47% of the time is the tell: the misses
are more likely derived figures than wrong lookups.

### The work

Take the answers from the completed 30-Q baseline run and classify a sample of
**every failing number** (aim for 40–60) into:

| bucket | meaning | correct response |
|---|---|---|
| **A. derived** | legitimate arithmetic (difference, sum, ratio, average, per-unit) | metric bug — scope the metric |
| **B. scale/unit** | right fact, wrong magnitude ("$97.7B" vs a store value in thousands) | real bug in synthesis or `parse_numbers` scaling |
| **C. genuinely wrong** | no such figure exists | real bug — this is what the metric is *for* |
| **D. stranded text** | number sits in text left behind by a failed excision | already fixed 2026-07-26; should be ~0 in the new run |

Write the classifier as a script under `scratchpad/`; it needs
`eval/reports/agent-<ts>.json` plus `data/ingest.db`. Report the bucket split.

### Decision rule

- **Mostly bucket A** → the metric is mis-specified. Fix the *metric*, not the
  system: restrict it to figures the answer presents as filed values, or score
  derived figures against a recomputation instead of the store. Then re-baseline.
  Do not touch retrieval.
- **Mostly B** → fix scale handling; check `_context_scale` and the "(In
  millions)" table convention in the corpus.
- **Mostly C** → this is the real thing. *Now* retrieval/synthesis work is
  justified, and only now.

**Do not raise the tolerance to make the number go up.** That converts a real
metric into a meaningless one — the same failure mode as the retraction bug.

---

## Task 2 — `citation_accuracy`: why do cited spans not support their claims?

`citation_accuracy` (`metrics.py:114-120`) is the fraction of judged
`(claim, span)` pairs the `eval_judge` rules as supported. Measured on the
matched 5-question pair (2026-07-26):

| arm | citation_accuracy | claims judged |
|---|---|---|
| GLM 5.2 | 0.667 | 15 |
| Opus 5 | 0.381 | 21 |

This is a **different failure** from `numeric_fidelity` and should not be
lumped together with it. Note the tension worth explaining: verifier
precision/recall is ~0.98, so the verifier judges claims against their sources
accurately — yet the judge says a third to two-thirds of cited spans don't
support their claim. Both cannot be a simple accuracy story; one of them is
measuring something other than what its name suggests.

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

## What is explicitly out of scope here

- **Model selection.** Settled: stay on GLM 5.2 on cost. See the head-to-head
  in `phase-3-exit-gate.md`. Re-opening it is not how these metrics get fixed.
- **Retrieval parameters.** ADR 0011 is settled on verified labels
  (`openai` + no rerank). Do not re-tune retrieval until Task 1 shows bucket C
  dominates — otherwise you are tuning against a mis-specified metric.
- **`grounding_rate` 0.833 → 0.90.** The smallest of the three gaps and the
  most likely to move once the other two are understood. Leave it last.
