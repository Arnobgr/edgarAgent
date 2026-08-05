# ADR 0038 — An answer with no claims is a failed run, not a successful one

**Date:** 2026-08-05 · **Phase:** 5 (found while checking a Phase 5
dependency) · **Status:** accepted — **§1 and §2 implemented 2026-08-05**;
§3 is bookkeeping the pre-public-deploy eval run settles · **Extends:**
ADR 0015 (the denominator never shrinks silently), ADR 0021/0023 (retraction
and `answer_span`), ADR 0025 (a guardrail abort costs an attempt), ADR 0027
(recorded runs are the demo surface)

## Context

Run `2b11fa1d4f9e` of the accepted 2026-08-01 baseline answers *"How much did
Nvidia spend on research and development in its latest fiscal year"* with a
434-character draft stating **$18,497,000,000**, and it carries **zero
claims**. Not zero surviving claims — zero in every checkpoint the run ever
wrote. Read it back with:

```python
from langgraph.checkpoint.sqlite import SqliteSaver
with SqliteSaver.from_conn_string("data/checkpoints.db") as saver:
    tup = saver.get_tuple({"configurable": {"thread_id": "2b11fa1d4f9e"}})
    cv = tup.checkpoint["channel_values"]
    len(cv["draft_answer"]), len(cv["claims"]), len(cv["verdicts"])   # 434, 0, 0
```

`critic_passed` is `True` and `error` is `None`. **The run reported success.**

Every stage behaved correctly on an empty set. `synthesize` returned
`output.claims == []`, so `claims` is `[]`. `graph.add_edge("synthesize",
"verify")` is unconditional, so `verify` ran and gathered over nothing.
`retract` had no span to excise. `critic` judged the prose and passed. Then
`build_run_result()` iterated `state.claims`, produced no `GroundingRow`, and
returned a `RunResult` whose `grounding` and `retracted` are both empty.

**No gate metric can see this.** `grounding_rate` and `hallucination_rate`
both pool claims across runs:

```python
for result in results:
    for row in result.grounding:   # this run contributes no rows at all
        ...
return grounded / total if total else 0.0
```

Zero claims adds 0 to the numerator *and* 0 to the denominator. The run is not
scored badly; it is not scored. `n_completed` counted it, so the baseline
reads 30/30 and `eval/baselines.json` was written and accepted.

So a specific dollar figure was delivered as fact, with no verification of any
kind, through the one path the exit gate is structurally blind to. That is the
failure this project exists to prevent, and it is the second time a metric has
read clean for the wrong reason — the first being the pre-2026-07-26
`hallucination_rate: 0.000` that hid surviving hallucinated text.

The run is also one of the 30 in `recorded/`, which ADR 0027 makes the static
demo surface. Rendered, it is an answer above an empty grounding table.

**This is a missing invariant, not a metric bug.** The system assumes a
delivered answer has claims. Nothing asserts it.

## Decision

**Three changes, smallest first.** §1 and §2 landed on 2026-08-05, in
`synthesize` and the eval runner respectively; §3 stands.

### 1. `synthesize` retries once on an empty claim list

`repair_spans()` in `src/fie/graph/build.py` is already exactly this shape: a
bounded, single re-ask when the structured output is internally defective, on
the same messages, keeping the original if the retry is no better. An empty
claim list is the same class of defect and gets the same treatment, in the
same node, rather than a new graph edge.

If the retry also returns no claims, set `error` on the state. `guarded()`
already routes an `error` to END with a partial result, and `_run_gold`
already retries on `error` as well as on exceptions (ADR 0025) — so an empty
synthesis costs an attempt and shows up in `degraded_attempts` instead of
scoring silently. **Reuse that path; do not add a parallel one.**

### 2. `eval-agent` reports `runs_with_no_claims`

A count beside the metrics, in the report and in `baselines.json`. It is
**not** in `TARGETS` and must not gate — the point is that the number stops
being invisible, not that it starts failing runs. A future session can decide
whether it earns a target once there is more than one observation.

### 3. The accepted baseline is 29 verifiable runs of 30, and says so

Do **not** re-run the full eval to fix this: $0.69 and 75 minutes to restate a
number already known within judge noise. The pre-public-deploy full run is
already mandated (ADR 0036) and will supersede it with a clean 30. Until then
`eval/baselines.json` keeps its numbers and CLAUDE.md records the correction —
`grounding_rate` 0.972 was measured over 29 runs' claims, not 30.

`recorded/` gets regenerated from that same run. Until it does, **the Phase 5
landing page must not select `2b11fa1d4f9e`**, because a demo whose first
screen is an unverified answer inverts the thing being demonstrated.

## Tests

- `tests/test_graph.py` — `StructuredFakeModel` queues a synthesis output with
  `claims=[]`; assert the retry fires and its claims reach the state. Queue a
  second empty output; assert `error` is set and the run ends with a partial
  result rather than a clean one. This is the invariant, and a `0.000` on any
  future run should be re-checked against it the way the retraction invariant
  is.
- `tests/test_eval_metrics.py` — a `RunResult` with an empty `grounding`
  raises `runs_with_no_claims` and moves neither `grounding_rate` nor
  `hallucination_rate`. That assertion is the whole finding, frozen.

## Consequences

An empty synthesis becomes a retried, then failed, run. `_run_gold` drops
nothing — it keeps the result either way, so no denominator shrinks silently
(ADR 0015), and the attempt is visible.

The change is in the synthesis node and the eval only. Retrieval, prompts and
models stay cleared; this adds a structural check, it does not tune anything
against a gate metric.

## Honest limits

- **One observation.** 1 run in 30 on 2026-08-01, and the synthesis role was
  on the GLM challenger arm for that run, as it has been for every run so far.
  Whether an empty claim list is model-specific is untested, not disproven —
  the same caveat ADR 0025 records for stalled synthesize calls.
- A retry fixes the symptom. **It does not explain why a model that produced a
  cited figure emitted no claim for it**, and the checkpoints cannot answer
  that because the raw structured output is not stored, only the mapped
  `Claim` list.
- `runs_with_no_claims` counts the failure mode we found. It does not
  generalise to "the answer contains an assertion no claim covers" — a claim
  list that is non-empty but incomplete would still pass unnoticed. That is a
  larger problem and this ADR does not solve it.
