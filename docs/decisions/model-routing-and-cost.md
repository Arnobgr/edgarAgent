# Model routing and cost — what a verified answer costs, and why it is routed this way

**Date:** 2026-10-09 · **Phase:** 7 (Deliverable 1) · **Status:** accepted ·
**Relates to:** ADR 0001 (routing by role), ADR 0014 (provider-agnostic router),
ADR 0032 (Langfuse Cloud in production), ADR 0045 (GLM 5.2 for synthesis/critic)

## Summary

On the accepted baseline (`agent-20260813-174221`, 30/30, every floor met) an
analyst question costs **$0.036 on average, $0.025 at the median and $0.070 at
p90**; the most expensive of the 30 cost $0.104. Those are the prices the run
was charged. GLM 5.2 has since been repriced, and the same tokens at the
2026-10-09 price cost **$0.021 on average and $0.052 at p90**. Three quarters of that is
synthesis. What drives it is **how many times the answer gets drafted**, not
how big the question is: a run that verifies cleanly on its first draft costs
$0.016, and one that loops back costs $0.057.

Synthesis and critic stay on GLM 5.2. The reason is **cost plus "it clears
every floor"**. GLM has not been shown to beat the Anthropic arms on quality,
because no full-size Anthropic arm was ever run against the current system.
That gap is stated in the A/B section below, with what it would cost to close.

## How this was measured

There are two independent sources, and they agree.

- **Checkpoints.** `RunBudget` is cumulative and checkpointed after every
  superstep, so the difference between two consecutive checkpoints is what
  that superstep spent. The node that ran is identified by the channels it
  wrote. This gives cost per run and per node, priced at `Settings.model_prices`.
- **Langfuse traces** (the local dev instance the baseline ran against). Every
  generation carries its `fie-role:<role>` tag (ADR 0001) and its input/output
  token counts, reasoning included. This gives the input/output split per role,
  which is what repricing the alternatives requires.

Repricing the Langfuse token counts at the same unit prices reproduces the
checkpoint cost to the cent on 27 of the 30 runs. The other three differ by
about $0.0001. Both sources cover the production answer path only: the
`eval_judge` role scores the eval and never runs on a user's question, so it
is excluded.

## Where the money goes

Mean per question, 30 runs, at the prices the run was charged:

| Role | Model | Calls | Input tok | Output tok | $ / question | Share |
|---|---|---|---|---|---|---|
| `synthesis` | GLM 5.2 | 1.8 | 20,215 | 2,724 | 0.0273 | 75% |
| `verify_qualitative` | Gemini 3.5 Flash-Lite | 5.2 | 6,642 | 482 | 0.0032 | 9% |
| `fact_mapping` (numeric verify) | Flash-Lite | 3.9 | 2,540 | 516 | 0.0021 | 6% |
| `critic` | GLM 5.2 | 1.1 | 1,184 | 174 | 0.0016 | 4% |
| `fact_lookup` (research) | Flash-Lite | 2.6 | 1,677 | 138 | 0.0008 | 2% |
| `verify_forward` | Flash-Lite | 0.8 | 905 | 73 | 0.0005 | 1% |
| `query_rewrite` (research) | Flash-Lite | 2.6 | 689 | 78 | 0.0004 | 1% |
| `decompose` | Flash-Lite | 1.0 | 341 | 64 | 0.0003 | 1% |
| **total** | | **19** | **34,193** | **4,249** | **0.0362** | |

By node: synthesize 75.5%, verify 15.8%, critic 4.5%, research 3.5%,
decompose 0.7%. Retract makes no LLM call. Numeric verdicts are a SQLite
lookup, and the model's only part in them is mapping claims to concepts
(ADR 0012), which is why verification costs **$0.0008 per claim**.

Synthesis reads about 20k tokens of packed evidence and writes about 2.7k.
Which side of that dominates the bill depends on the price, and the price has
moved. At the $0.95/$2.98 this run was charged, input was 70% of the synthesis
bill. At the $0.057/$4.30 OpenRouter charges on 2026-10-09, it is 9%. **On
today's prices, output length is the lever and evidence packing is nearly
free.** A conclusion about where to cut that does not name its price date is
not one.

## What scales the cost

| Shape of the run (30 runs) | mean | median | range |
|---|---|---|---|
| sub-questions | 1.5 | 1 | 1–4 |
| research branches, all passes | 2.6 | 2 | 1–8 |
| claims | 7.1 | 5.5 | 1–19 |
| synthesis passes | 1.6 | 1.5 | 1–3 |

Fitting cost against each of these separately:

| Driver | $ per unit | r |
|---|---|---|
| synthesis pass | 0.034 | **0.80** |
| sub-question | 0.015 | 0.45 |
| claim | 0.0028 | 0.56 |

**Loop-backs are the cost driver.** 15 runs verified on the first draft and
averaged **$0.016**. The 15 that looped (verify → targeted research → a second
synthesis, or critic → synthesize) averaged **$0.057**, 3.6× as much. The
reason is that each loop pays for another full synthesis over a larger fan-in.
Claims are cheap at the margin, because the verify fan-out runs on the cheap
model and the numeric path barely uses a model at all.

So the honest scaling statement is: **about $0.016 for a question that is
right the first time, plus about $0.034 for every redraft.** The iteration caps
on the conditional edges (three cycles, all bounded) are what keep that a
small integer.

## The alternatives

Same per-role token counts, different unit prices (per million tokens,
input/output). Anthropic prices are Anthropic's direct list prices as of
2026-10-06. GLM prices are what OpenRouter charges on 2026-10-09; the rest
are from its public models endpoint on the same date.

| Routing | Reasoning roles | Mechanical roles | Mean $/q | p90 $/q | × chosen |
|---|---|---|---|---|---|
| **Chosen** (prices as charged) | GLM 5.2 $0.95/$2.98 | Flash-Lite $0.30/$2.50 | **0.036** | **0.070** | 1.0 |
| **Chosen, at the 2026-10-09 charge** | GLM 5.2 $0.057/$4.30 | Flash-Lite $0.30/$2.50 | **0.021** | **0.052** | 0.6 |
| GLM 5.3 (untested) | $0.0312/$4.40 | Flash-Lite | 0.021 | 0.051 | 0.6 |
| Sonnet 5.5 (or Sonnet 5) | $2/$10 | Flash-Lite | 0.079 | 0.149 | 2.2 |
| Opus 5.5 | $4/$20 | Flash-Lite | 0.151 | 0.291 | 4.2 |
| Opus 5 / 4.8 (the ADR 0001 default) | $5/$25 | Flash-Lite | 0.187 | 0.361 | 5.2 |
| Everything frontier | Opus 5.5 | Opus 5.5 | 0.222 | 0.469 | 6.1 |
| Everything cheap | Flash-Lite | Flash-Lite | 0.021 | 0.044 | 0.6 |
| Cheaper mechanical tier | GLM 5.2 | Haiku 5.5 $0.10/$0.50 | 0.031 | 0.065 | 0.9 |

**This table understates the Anthropic arms by about 2×.** It holds token
counts fixed, but the head-to-heads below measured the models' real behaviour,
and the Anthropic models wrote more. They used adaptive thinking, and Opus
emitted 40% more claims. The measured multiples were **5.1× for Sonnet 5 and
10.3× for Opus 5**, against 2.2× and 5.2× above. Read the table as a lower
bound.

**On the public path this is not a matter of taste.** The public run cap is
$0.25 (`public_budget_max_cost_usd`), and the daily governor is $0.35. At the
chosen routing the worst of the 30 runs ($0.104) leaves 2.4× headroom under
the run cap, and the daily cap admits 9 average questions. On Opus 5.5
the modelled p90 ($0.29) is *over* the run cap, so one public question in ten
would abort partway, and the daily cap would admit about 2. A frontier
synthesis route would mean redesigning the governor as well as paying more.

## The A/B the decision rests on, stated plainly

This section corrects ADR 0045. That ADR says the A/B runs "are now complete".
Runs exist, but all of them are small, and all were made in July on an earlier
system:

| Date | Questions | Arm | grounding | citation acc. | $/q (measured) |
|---|---|---|---|---|---|
| 07-23 | g-001–010 | **GLM 5.2** | **0.511** | — (judge skipped) | **0.042** |
| 07-23 | g-001–010 | Sonnet 5 | 0.288 | — | 0.215 |
| 07-26 | g-001–005 | **GLM 5.2** | **0.833** | **0.666** | **0.033** |
| 07-26 | g-001–005 | Opus 5 | 0.636 | 0.380 | 0.339 |
| 07-23 | g-001–003 | Grok 4.5 | 0.833 | — | 0.082 |

Reports `agent-20260723-162646`, `-170240`, `20260726-212641`, `-212038` and
`20260723-153610`. Three caveats apply to all of them:

- **They predate the instrument that now gates.** Every hallucination reading
  from before 2026-07-26 is unreliable (see CLAUDE.md, "Retraction is
  load-bearing"). That covers the Sonnet arm's 0.096 and GLM's 0.000 alike, so
  that column is left out above. ADRs 0021–0023 and 0038–0044 have since
  changed both the answer path and the measurement.
- **The Sonnet gap was never explained.** It was first blamed on the
  forced-tool-calling-under-thinking bug. That diagnosis was withdrawn after
  36 isolated calls failed to reproduce a single parse failure, and every
  question completed. It is a real signal of unknown cause, not a known
  plumbing fault.
- **The Opus comparison is confounded by claim count.** `grounding_rate` is
  per-claim, and Opus emitted 40% more claims. n = 5.

Every full 30-question run in `eval/reports/`, all eight of them, used GLM. So the decision
stands on three things. First, **cost**: a measured 5–10× against the
Anthropic arms. Second, **sufficiency**: GLM clears every floor on the current
baseline (grounding 0.980, hallucination 0.000, numeric fidelity 0.989,
citation accuracy 0.867). Third, **no head-to-head went against GLM**. That is
the blueprint's earn-it rule applied honestly: the expensive route has to earn
its price, and nothing measured says it would.

**What would reopen it, and what that costs.** One full `eval-agent` with
`synthesis`/`critic` on Sonnet 5.5 would settle the quality question on the
current system. On the measured multiple that is about **$5 of synthesis**
plus the judge, which costs the same for both arms, and about 75 minutes. Run
it if GLM ever misses a floor, if the monthly drift run moves, or if a
reviewer needs the quality comparison rather than the cost one.

## Verifier routing

Decision 3.5 (`plans/00-overview.md`) escalates a verifier role to the
frontier model only if its claim type measures below **0.90 precision / 0.85
recall** on the labelled claim set (`eval/verifier_claims.jsonl`, 128 claims).

| Claim type | Labelled claims | Phase 3 exit gate P / R | Current baseline P / R |
|---|---|---|---|
| numeric | 110 | 0.979 / 0.979 | 1.000 / 1.000 |
| qualitative | 18 | 1.000 / 1.000 | 1.000 / 1.000 |
| forward-looking | **0** | not measured | not measured |

**No escalation, so the escalated path costs nothing.** What escalation would
have cost: putting `verify_qualitative` and `verify_forward` on Sonnet 5.5
raises the verify node from $0.006 to about $0.023 per question. That
+$0.017 is nearly half again the whole bill, spent on a verifier that already
measures 1.000.

Two corrections to the record:

- The exit-gate plan says "ADR 0013 records 'no escalation'". ADR 0013 was
  never written; the numbering goes from 0012 to 0014. The decision and its
  numbers live in `plans/phase-3-exit-gate.md` and now here.
- **The forward-looking verifier has never been measured.** The labelled set
  holds no forward-looking claims. It is about one call in 24 (0.8 per question) and
  1% of cost, so the money at stake is small. But "no escalation" is a measured
  decision for two claim types and an assumption for the third. The fix is to
  add forward-looking claims to the set, not to escalate the role.

## Build vs buy

**OpenRouter for the routes where price is the point; Anthropic direct where
it is not.** Every reasoning and mechanical role goes through OpenRouter,
because those are the calls worth price-shopping. That is how a four-arm
comparison (GLM, Sonnet, Opus, Grok) was a config change (ADR 0014). It is also why GLM's input price could fall from $0.95 to
$0.057 without a line of code changing. The one Anthropic role is the eval
judge, which is called natively because it is the measuring stick, and because
an Anthropic model on OpenRouter's OpenAI-compatible path is the
structured-output failure ADR 0014 bans.

**Langfuse, not LangSmith, because the data-control option has to exist even
when it is not taken.** Langfuse is open source and self-hostable, and the dev
stack runs it locally (six containers). Production traces go to Langfuse Cloud
only because those six containers do not fit beside five other services on the
VPS (ADR 0032). That is a measured capacity decision with a recorded trigger
for bringing it home. LangSmith is a SaaS product first. Choosing it would
have made "who holds the traces of every analyst question" a vendor's answer
rather than ours. Tracing is also not a health dependency (ADR 0032), so a
SaaS outage cannot turn the service red.

**The fluency paragraph.** A verified answer to an analyst question over SEC
filings costs $0.025 at the median and $0.036 on average, under $0.11 in the
worst case of 30. About $0.016 of it is the first pass, and each redraft that
verification forces adds about $0.034. Three quarters goes to one role,
synthesis. At today's prices its output is 90% of its bill, so how much it
writes, and how often it is made to redraft, matters more than how much
evidence it reads. Numeric truth costs almost nothing, because it
is a SQLite lookup rather than a model's opinion. Routing is per role and is
config: reasoning on an open model at a fifth to a tenth of the frontier price,
mechanical work on a flash-tier model, and the judge on Anthropic. That held
every quality floor. The comparison that would tell us what a frontier model
buys on quality costs about $5 and has not been run, because no floor has
asked for it.

## Follow-ups this note found

- **`model_prices` was stale in two places — fixed 2026-10-09.** Sonnet 5
  was listed at $3/$15 (actual $2/$10). GLM 5.2 was at $0.95/$2.98, and
  OpenRouter charges $0.057/$4.30, confirmed on the billing side. The tracker
  feeds the public spend governor, so this changes what the caps admit: the
  daily $0.35 now covers about 17 average questions, up from 9. The
  run-cap headroom over the worst of the 30 runs rises from 2.4× to 3.8×.
- **GLM 5.3 is priced below 5.2** ($0.0312/$4.40). At this token profile it
  saves nothing measurable ($0.0206 against $0.0209), because the bill is now
  output-bound and its output price is higher. A 5.3 arm would be a quality
  test, not a cost one.
- **Haiku 5.5 is now cheaper than Flash-Lite** ($0.10/$0.50 against
  $0.30/$2.50) and would cut about $0.005 a question. Quality on those roles is
  unmeasured, and CLAUDE.md says models are cleared and not to be tuned for
  metrics. Recorded as an option, not a recommendation.
- **Forward-looking claims in `verifier_claims.jsonl`.** See "Verifier routing".
