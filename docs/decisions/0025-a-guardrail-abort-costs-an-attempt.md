# ADR 0025 — A guardrail abort costs an attempt, not the baseline

**Date:** 2026-08-01 · **Phase:** 3 (Stage E) · **Status:** accepted ·
**Extends:** ADR 0015 (the denominator never shrinks silently), ADR 0016
(`--write-baseline` is gated)

## Context

Stage E's re-baseline was refused twice. The second attempt read 29/30 with
`hallucination_rate` 0.049 against a 0.02 target and `expected_fact_coverage`
0.750, and nothing was wrong with the system it measured.

**A guardrail abort arrives as a return, not an exception.** `guarded` catches
`TimeoutError`, puts `error` on the state and returns; every downstream node
short-circuits and the graph routes to END. `answer_question` therefore hands
back a degraded `RunResult` that no `except` clause sees. `_run_gold` had a
retry for exactly this class of infra failure — added for the recurring
`SQLITE_MISUSE` death — but it was wrapped around the call, so it caught only
what raised. Neither `grounding_rate` nor `hallucination_rate` inspects
`result.error`; only `declined_unanswerables` does. The degraded run scored.

Two synthesize calls stalled at the 600s cap in that run, and they failed in
different places:

| | g-009 | g-010 |
|---|---|---|
| which synthesize call | the **first** | the **second**, after a verify loop-back |
| what survived | nothing — an empty answer | the stale first draft, 8 unsupported claims, never retracted |
| what it cost | 3 expected facts scored `absent` | 8 unsupported survivors |

Alone, those two decided the run: without them it reads `hallucination_rate`
0.000, `expected_fact_coverage` 0.857, `grounding_rate` 0.948 — a pass.

**This is not rare.** Recovered from checkpoint timestamps across every eval
run: a synthesize call stalls in **3 of 198** calls at the current 600s cap,
and did in 8 of 223 at the old 300s cap. Stalls land *exactly* on the cap
while clean calls run 21s median and 206s max — the distribution is bimodal,
so these are hangs, not slow work. Doubling the cap roughly halved the rate,
which says the 300s cap was also clipping legitimate re-synthesis. At ~70
synthesize calls per 30-question run, an unretried clean 30/30 was a **1-in-3
event**, and one run in six had achieved it.

## Decision

**Retry a question whose `RunResult.error` is set, on the same one-retry
budget as an exception.** A stall costs an attempt instead of the baseline.

**Keep the result on both attempts.** If a question degrades twice it stays in
`results` carrying its bad numbers. Dropping it would shrink every metric's
denominator to hide a failure — the defect ADR 0015 already closed once, and
trading a visible bad number for an invisible one is not an improvement.

**Report `degraded_attempts`.** The retry keeps stalls out of the metrics, so
without a counter the run's real infra cost becomes invisible. It is
informational and absent from `TARGETS`: gating on it would refuse a run for
a provider hiccup.

Not chosen: lowering `node_timeout_seconds` below 600. Clean calls reach 206s
and the 300s cap demonstrably clipped real work, so a tighter cap trades a
rare stall for a routine abort. Not chosen: switching the synthesis model.
Every run to date has had GLM on synthesis, so whether the model causes the
stalls is **untested, not disproven** — and there is no way to attribute a
model's effect while a single stall can destroy a 75-minute run. This change
is what makes that A/B measurable.

## Consequences

The next run passed 30/30 and became the accepted baseline
(`agent-20260801-122527`): `grounding_rate` 0.972, `hallucination_rate` 0.000,
`numeric_fidelity` 0.963, `expected_fact_coverage` 1.000, `citation_accuracy`
0.851, verifier P/R 1.000.

**Be plain about what that does and does not prove.** `degraded_attempts` was
**0** — no call stalled, so the retry never fired. It is proven by the two
regression tests in `tests/test_agent_runner.py`, not by this baseline. The
run landed in the ~1-in-3 clean band; the change insured against losing it
rather than earning it.

**The eval is protected; the product is not.** The same abort bypasses
`retract`, because an errored state routes straight to END. Whatever the last
synthesize produced is returned with its unsupported claims intact — `fie ask`
would have shown a user g-010's 1158-character draft carrying 8 of them. That
is the same surviving-hallucinated-text failure ADR 0021 closed for excision
failures, reached by a different route, and it means "retraction is
load-bearing" holds only on the paths that reach `retract`. Recorded as open
item 5 in `plans/phase-3-recall-and-retraction.md` with its two candidate
fixes; it needs its own ADR and was deliberately not bundled here.

## Honest limits

- Three stall events at the current cap is a small sample. Treat 1.5% as an
  order of magnitude, not a rate.
- Retrying until a question does not error is, in a weak sense, selecting for
  lucky runs. The argument for it is that the metrics measure answer quality,
  not provider uptime, and that the exception retry already conceded the
  point. `degraded_attempts` exists so the concession stays visible.
- A retry overwrites `run_ids[question.id]`, so the failed attempt's run id is
  absent from the report. The error is logged with its run id; the checkpoint
  database still holds the thread.
