# ADR 0039 — The baseline ratchet gates on membership, so a noisy metric must not be a member

**Date:** 2026-08-06 · **Phase:** 5 · **Status:** accepted — implemented
2026-08-06; **widened by ADR 0043 on 2026-08-13**, which applies this
decision's own rule to the three metrics still in the ratchet. The mechanism
below is unchanged and remains the reference for it; what is superseded is the
scope — this ADR fixed `citation_accuracy` and left the same defect armed
elsewhere, including on two metrics that had already fired on the very run
analysed here. · **Extends:** ADR 0015 (`--write-baseline` is gated), ADR 0024
(what moved `citation_accuracy` in July), ADR 0036 (the monthly drift run),
ADR 0038 (a count recorded beside `metrics`, never inside it)

## Context

The pre-public-deploy full eval that ADR 0036 mandates ran on the VPS on
2026-08-06, in a one-off container off the deployed image
(`sha256:255d74f0…`), against the service's own corpus. It completed 30 of 30
questions and **exited 1**.

| | 2026-08-01 baseline | 2026-08-06 drift run | `TARGETS` |
|---|---|---|---|
| `grounding_rate` | 0.972 (211/217) | 0.955 (190/199) | ≥ 0.90 — pass |
| `hallucination_rate` | 0.000 | 0.000 | ≤ 0.02 — pass |
| `numeric_fidelity` | 0.963 (103/107) | 0.924 (85/92) | ≥ 0.85 — pass |
| `expected_fact_coverage` | 1.000 | 0.893 (25/28) | ≥ 0.65 — pass |
| `citation_accuracy` | 0.851 (177/208) | 0.828 (154/186) | absent |

`degraded_attempts: 1`, `runs_with_no_claims: 0`, `unanswerable_declined:
1.000`, verifier precision and recall 1.000 on all three claim types, 0 judge
failures.

**Every gated target passed, and the run still failed.** The non-zero exit came
from the other gate.

### Two gates, and only one of them has a list

`TARGETS` holds absolute floors and is explicit about its members. The
*baseline ratchet* — `_gate(metrics, baseline_metrics)` — has no list at all:

```python
for name, baseline in baselines.items():   # whatever the last run wrote
```

It iterates the `metrics` block of `eval/baselines.json`. So the set of gated
metrics is decided by what `--write-baseline` chose to serialise, and
`citation_accuracy` was in that `startswith` tuple. **Writing the number
enrolled it.** Nothing declared it a gate; nothing had to.

The rule was already known and already written down, one screen above the
tuple that breaks it — the comment explaining why ADR 0038's
`runs_with_no_claims` sits *beside* `metrics`: *"everything in there becomes a
gate on the next run via `_gate`."* CLAUDE.md meanwhile stated the opposite of
the behaviour: *"absent from `TARGETS`, so it never refuses a run."*

### Why this metric in particular must not ratchet

`citation_accuracy` is an LLM judgment over spans, and ADR 0024 measured its
self-disagreement: the judge flips on about **1 in 27 byte-identical spans**,
so a single reading carries **±0.037**. The 2026-08-06 delta was **0.023** —
comfortably inside the noise floor of the instrument that produced it.

A ratchet on such a number fails on coin flips, and it fails one-way: accept a
lucky-high reading and every later run must beat the luck. The cost is not a
red line in a report. It is `exit 1` on the monthly cron — an alarm that cries
wolf loses the ability to report the drift it exists to catch (ADR 0036), which
inverts the job.

This is the same shape as three other findings in this repo: the
pre-2026-07-26 `hallucination_rate: 0.000` that hid surviving text, the
claimless run of ADR 0038, and — the same week — a fail2ban jail reading the
wrong source while reporting healthy. **A check that is not measuring what it
says it measures.**

## Decision

**1. The gated set is named once, where it is chosen.** `_ratcheted()` and
`_RATCHETED_PREFIXES` in `agent_runner.py`, carrying the rule in a comment: a
metric belongs there only when a drop is evidence of a worse system rather than
a different reading of the same one. The alternative — an exclusion list inside
`_gate` — was rejected: it would create a second place where "what gates" is
decided, which is the disease, not the cure.

**2. `citation_accuracy` moves beside `runs_with_no_claims`**, at the top level
of `baselines.json`. It stays recorded, so a real move remains visible across
baselines and the spot-check sample keeps its context. It stops ratcheting.

**2b. It gets an absolute floor of 0.75 in `TARGETS`, and `TARGETS` is now
checked on every path.** Un-ratcheting alone would have left it watched by
nothing: `_gate(metrics, TARGETS)` ran under `--write-baseline` only, so an
ordinary run — and **every monthly drift run is an ordinary run** — was gated
by the ratchet and by nothing else. A genuine collapse to 0.40 would have
printed the number and exited 0. That is a worse failure than the one this ADR
started with, and it was introduced by the first version of this fix.

A floor is the right instrument where a ratchet was the wrong one: it is
insensitive to ±0.037 of judge noise and still catches the case worth an exit
code — cited spans not supporting their claims at all. **0.75 is deliberately
coarse.** It sits more than twice the measured noise below both readings we
have (0.851, 0.828), and two readings do not support a tighter number. Picking
a threshold from ambition rather than measurement is exactly how
`numeric_fidelity`'s 0.95 came to refuse a legitimate baseline (ADR 0015);
this errs the other way on purpose. Raise it as readings accumulate.

Checking `TARGETS` on both paths is redundant for the ratcheted metrics, whose
baselines already sit above their floors, and it is the only check a
non-ratcheted metric has.

**3. The accepted `eval/baselines.json` is edited in place** to move the
existing 0.851 out of `metrics`. Fixing only the writer leaves the armed file
behind until a new baseline is written — months away by design (ADR 0036) —
so the next monthly run would have failed the same way. The value is unchanged;
only its position is.

**4. The 2026-08-06 run is a passing drift run and is not promoted to
baseline.** `degraded_attempts: 1` disqualifies it under ADR 0025, and a drift
run must not move the line it is measured against. Its report stays at
`eval/reports/agent-20260806-143052.json`.

## Tests

- `tests/test_agent_runner.py::test_citation_accuracy_is_floored_not_ratcheted`
  — asserts both halves of the de-gating, because either alone leaves the bug
  live: the writer no longer enrols it, *and* the shipped `baselines.json` the
  next run actually reads no longer carries it in `metrics`. Then asserts the
  floor from both sides: 0.40 fails, 0.828 passes.

## Consequences

The monthly cron now exits 0 when every floor holds and no ratcheted metric has
regressed, and fails on either. `citation_accuracy` becomes what CLAUDE.md
always claimed it was — with a floor, which CLAUDE.md never claimed and which
it needed.

**A drift run is now gated by `TARGETS` as well as by the baseline**, which it
was not before. Nothing changes for the metrics already ratcheted; the exposure
this closes is any metric deliberately held out of the ratchet, of which there
is currently one.

Re-running the eval to confirm was rejected on ADR 0038's reasoning: $0.69 and
75 minutes to restate numbers already in hand. The change is in the eval
harness only — no retrieval, prompt, or model was touched.

## Honest limits

- **One reading cannot separate run-to-run variance from GLM drift**, and this
  ADR does not claim to. Nothing on the answer path changed between 2026-08-01
  and 2026-08-06.
- **The `expected_fact_coverage` drop is fully explained; the other two are
  not.** All three absent facts are g-016, the single degraded run — synthesis
  returned no claims, the ADR 0038 repair returned none either, and the retry
  produced an answer missing all three figures. With g-016 whole the metric is
  28/28. But `grounding_rate` and `numeric_fidelity` also fell, and both
  denominators shrank (199 claims vs 217, 92 figures vs 107) — answers were
  shorter, and why is unestablished.
- **ADR 0038 §3 stays open.** This run was expected to supersede the
  29-verifiable-runs-of-30 baseline with a clean 30. It got the clean 30
  (`runs_with_no_claims: 0`) but carries a degraded attempt, so it cannot be
  accepted. `recorded/` is still generated from the 2026-08-01 run.
- **The GLM structured-output failures are logged and unexplained.** Nine
  `synthesis returned no claims` warnings, all but one recovered by the single
  re-ask, plus three `EmptyStructuredOutputError: the model returned no tool
  call` absorbed by the retry ladder. Whether that rate is normal for this arm
  is unknown — there is no prior run with this logging to compare against.
