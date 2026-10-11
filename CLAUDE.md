# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`fie` — Financial Intelligence Engine: a multi-agent verification system that
answers analyst questions over SEC filings (TSLA/AAPL/NVDA, EDGAR-only) where
every factual claim is verified against a source or explicitly retracted.

The design spec is `financial-intelligence-engine-blueprint.md` (gitignored,
local-only). `plans/00-overview.md` resolves its open decisions and defines
conventions; each phase has a self-contained plan in `plans/`. **Phase 3 is
complete. Phase 4 is deployed on the private path** (2026-08-05) — the service
answers real questions on the VPS, reachable over the tailnet and nothing else,
with both crons installed and each proven by an actual run. The corpus is on
the box and was counted equal to the developer's: 28,550 facts, 78 filings, 34
accessions, 4,181 Qdrant points, same dense+sparse config.

**The public path went up on 2026-08-06** — nginx vhost, Let's Encrypt cert,
Turnstile enforced, fail2ban armed on the real log, all four neighbouring
vhosts untouched (ADR 0035). `docs/deploy.md` is the procedure, written from
that deploy. **Phase 5 is complete — its exit gate closed on 2026-08-07**:
`scripts/burst-test.sh` ran against the live host, one live end-to-end ask
went through the real Turnstile challenge in a browser, Lighthouse passed on
the mobile preset, and the screenshot is `docs/grounding-table.png`. One dated
item outlives it at the foot of `plans/phase-5-frontend.md`; the monitoring
gaps left open *by choice* are in `docs/monitoring.md`. **Phase 6 is
security** — threat model, adversarial evals, and the writeup. **Steps 1–4 are
done as of 2026-08-12**; the writeup is `docs/security.md` **Part 2**, which is
the argument (architecture vs hardening, the evidence and its limits, residual
risk) while everything above it in that file stays the operational view. **The
unregressed full `eval-agent` ran on 2026-08-13 and is the new baseline** — it
went *up* on every metric, so the fence cost no grounding. **The exit gate
closed on 2026-08-13**: `main` carries the gate change and the deploy went out
the same day (image `sha-e7789b3`, `/healthz` all-true), and the Pages build
made `frontend/public/_headers` live — six headers where the CDN used to serve
two, CSP included and measured against the deployed bundle. One item outlived
it by ten days and is now closed too: the npm hold, taken 2026-08-23 (ADR 0041,
amendment).

**A regenerated `recorded/` changes every run id, and one of them is
hard-coded.** `frontend/src/lib/recorded.ts` names the run a stranger lands
on. Accepting the 08-13 baseline made the old id stop existing and **nothing
failed** — Cloudflare Pages answers a missing asset with the SPA shell under
**200**, so the page parsed `index.html` as a run payload and broke in the
browser while every check stayed green. This is the `/healthz` trap on the
other host. `test_the_landing_run_the_spa_hardcodes_is_actually_exported` in
`tests/test_export_runs.py` is the guard, and it is in pytest rather than
vitest on purpose: **CI runs no frontend step**, so a vitest assertion would
not have caught it.

**The adversarial set ran for the first time on 2026-08-12 — 16/16 with one
observation, and both of the day's two "failures" were the instrument, not the
system. Do not re-derive that.** Report
`eval/reports/adversarial-20260812-094531.json` (kept with `git add -f`, that
directory is ignored). All six poisoned-document cases passed **with the poison
confirmed in the evidence channel**, so none passed by never being retrieved.

**The rule that decided it is in `docs/security.md` story 2: a question may
control the *form* of the answer, never the *content*, and never extract.** A
question is an instruction channel by design ("briefly", "as a table"), so the
rule cannot be that it may not instruct. i-010 really did comply — the answer
opens `LIMA-3040.` and then reports NVDA's revenue correctly and grounded — and
that is *form*, whose attacker and victim are the same person. It is recorded
by `observe()` and never gated, the way `runs_with_no_claims` sits outside the
`metrics` block. i-001 was a refusal that named the token it was refusing;
`canaries` (checked anywhere, a leak) and `forbidden_opening` (checked at the
opening, permitted) are separate fields for that reason. **A poisoned case must
never use `forbidden_opening`** — there the victim is a different visitor.
p-001 plants the same instruction in a *chunk* and the run ignored it, which is
the fence working (ADR 0040), not an inconsistency.

**`/healthz` returns 200 whether or not it is degraded, so the assertion has
to live in the prober.** Qdrant, the facts store and `spend_ok` all report in
the *body*, and a monitor watching only the status line is green through
exactly the failure the first deploy produced. That is why
`docs/monitoring.md` specifies two **keyword** checks against the one URL and
not a plain HTTP check. Do not close the gap by returning 503 instead: the
container healthcheck and `deploy.sh`'s health wait both key off that 200.

`eval/baselines.json` holds run `agent-20261011-165218`, accepted 2026-10-11.
It ran on the box against image `64ef36d`: 30/30, `degraded_attempts: 0`,
`runs_with_no_claims: 0`, every floor met, and the ratchet held. It is the first
baseline with every `DraftClaim` field required (ADR 0046 addendum) and with
date-only claims judged by entailment (ADR 0047). The retraction invariant was
checked before acceptance: 15 claims retracted, all carrying spans, none still
in the delivered prose (two appear only in the retraction notice, which quotes
them), and none of the 230 rows scored `unsupported`. The 10-10 baseline before
it was the first with ADR 0044 (deltas recomputed from cited facts) and with
`claims` required. The 08-13 baseline was the first that was 30 verifiable runs
of 30, which closed ADR 0038 §3. The 08-06 column is the pre-public-deploy run
that ADR 0036 mandated, executed on the box against the deployed image. **It
passed every target and is not a baseline**, because `degraded_attempts: 1`
disqualifies it (ADR 0025):

| | **2026-10-11** | 2026-10-10 | 2026-08-13 | 2026-08-01 | 2026-08-06 | floor |
|---|---|---|---|---|---|---|
| `grounding_rate` | **0.974** | 0.968 | 0.980 | 0.972 | 0.955 | ≥ 0.90 |
| `hallucination_rate` | **0.000** | 0.000 | 0.000 | 0.000 | 0.000 | ≤ 0.02, ratcheted |
| `numeric_fidelity` | **0.936** | 0.978 | 0.989 | 0.963 | 0.924 | ≥ 0.85 |
| `expected_fact_coverage` | **1.000** | 0.893 | 1.000 | 1.000 | 0.893 | ≥ 0.65 |
| `citation_accuracy` | **0.849** | 0.782 | 0.867 | 0.851 | 0.828 | ≥ 0.75 |

**Both 10-10 drops had causes outside the answers, and both are fixed.**
Coverage lost all three of its facts in g-014. One OpenRouter backend omitted
the optional `source_chunk_ids` key there, so six correct figures were ruled to
cite nothing and retracted (ADR 0046 addendum). On 10-11 that backend omitted
no field in 126 claims, and coverage is back to 1.000. 11 of
`citation_accuracy`'s 20 extra rejections were g-024's dated court events, typed
`numeric`. The reconciler matched only their day-of-month and grounded "April
30, 2025" on a "September 30" snippet (ADR 0047). On 10-11 g-024 had 2
rejections in 16, against 11 in 19.

**The 10-11 `numeric_fidelity` drop is wording, not wrong numbers.** All six
unmatched figures are differences the answer computed ("total revenue grew by
$69,575 million" is 130,497 − 60,922). A difference is not a filed fact, so the
metric counts it as a miss by design. Four of the six are g-011, which chose to
state year-over-year deltas. The 10-10 run had two of the same kind.

**Only `hallucination_rate` is ratcheted (ADR 0043). Every other row is a floor
and nothing else, so the columns are readings, not thresholds.** Do not raise a
floor toward the best column — that is the ratchet spelled differently, and
every candidate raise sits above a reading that already happened.

**Do not read the 08-06 column as decay without reading ADR 0039 first.** All
three missing expected facts are one question (g-016, the degraded run), which
is the whole of that metric's drop. The other two fell with their denominators
— 199 claims against 217, 92 figures against 107 — and **one reading cannot
separate run-to-run variance from GLM drift**. Nothing on the answer path
changed between those two runs. The 08-13 column does not settle it either: it
came after the fence changed three prompts, so it is a different system, and it
moved *up*.

Retrieval, prompts and models remain cleared — do not tune them for gate
metrics. How the gate was reached is in ADRs 0018-0025 and in the two findings
documents (`docs/findings/2026-07-30-recall-decomposed-and-collateral-excision.md`
and the earlier `2026-07-28-...`), which remain accurate on mechanism with
three corrections recorded in ADR 0020.

**Four things about the instrument a session must not re-derive.**

**A guardrail abort decided a run once, and the cure is a retry, not a
tolerance** (ADR 0025). `guarded` turns a node timeout into `error` on the
state and the graph routes to END, so `answer_question` *returns* a degraded
`RunResult` that no exception marks. `_run_gold` now retries on `error` too,
not only on exceptions, and reports `degraded_attempts` — a run with a
non-zero count is not a baseline.

**Scores are floored; only an invariant is ratcheted (ADR 0043, which finishes
ADR 0039).** Two gates exist. `TARGETS` holds absolute floors and lists its
members; the *baseline ratchet* (`_gate(metrics, baseline_metrics)`) has no
list at all and gates on whatever sits in the `metrics` block of
`baselines.json` — **membership there is the gate**, and writing a number into
it enrols it silently. `_gate` has **no tolerance**: any drop of any size prints
`REGRESSION` and exits 1.

ADR 0039 found that mechanism through `citation_accuracy` (0.828 against 0.851
failed the 08-06 run on a delta of **0.023**, while the judge flips on about 1
in 27 byte-identical spans, so a reading carries **±0.037**) and fixed one
metric. **Three more were armed the same way and all three fired on that same
run** — `grounding_rate` 0.955 against 0.972, `expected_fact_coverage` 0.893
against 1.000, `numeric_fidelity` 0.924 against 0.963 — unread, because
`degraded_attempts` disqualified the run first. Four red lines, zero missed
floors.
ADR 0043 applied the rule to all of them: one claim of 200 is 0.005, one figure
of ~95 is ~0.011, one fact of 28 is 0.036, and the observed spread across four
post-ADR-0023 runs is 0.078 / 0.065 / 0.250. So `_RATCHETED_PREFIXES` is now
`("hallucination",)` — an invariant, not a score, with no noise band around
zero — and every scored metric has a floor, the `verifier_*` six included, since
leaving the ratchet without gaining a floor is un-watching rather than de-gating.
`TARGETS` is checked on every path, not only under `--write-baseline`; without
that half, un-ratcheting would leave a collapse to 0.40 exiting 0.

`baselines.json` is now two blocks with two jobs: **`measured` is the whole
reading and gates on nothing; `metrics` is the gate.** `runs_with_no_claims` and
`citation_accuracy` live in `measured` — no longer standalone keys.

Do not re-derive the diagnosis of why `citation_accuracy` moved in July: that
change was in the *claims*, not the citations (ADR 0024, commit `dcef747`).

**`hallucination_rate: 0.000` has two possible meanings** — see "Retraction is
load-bearing".

**An answer with no claims used to pass every gate** (ADR 0038 §1/§2, closed
2026-08-05). Run `2b11fa1d4f9e` of the accepted baseline states
`$18,497,000,000` in a 434-character draft carrying **zero** claims in every
checkpoint it ever wrote — nothing to ground, so nothing to fail, and
`n_completed` counted it. That was a hole in the instrument, not a bad answer.
`synthesize` now re-asks once and sets `error` if the second draft is also
claimless, so such a run costs an attempt and lands in `degraded_attempts`;
`eval-agent` reports `runs_with_no_claims` beside the metrics, ungated and
recorded in the `measured` block of `baselines.json` so it cannot become a gate.
**§3 closed on 2026-08-13**, two runs later than expected: the 08-06 run reached
a clean 30 but carried `degraded_attempts: 1`, and the 08-13 run reached a clean
30 with none, so it is accepted and `recorded/` is generated from it — **30 run
files where the 08-01 export had 29**. The `2b11fa1d4f9e` answer described above
is no longer on the recorded surface.

**`SynthesisOutput.claims` is required on purpose, and so are
`DraftClaim.source_chunk_ids` and `answer_span`. Never give any of them a
default** (ADR 0046 and its addendum). A default removes it from the tool schema's `required` list. One
GLM backend on OpenRouter then omits the key in most drafts, and the omission
parses as `[]`. That made 9 degraded attempts on 2026-10-09 look like a
transient upstream outage, which was the first, wrong diagnosis. The same
backend dropping `source_chunk_ids` retracted six correct figures in g-014.

## Working guidelines

Adapted from Karpathy's CLAUDE.md
(github.com/multica-ai/andrej-karpathy-skills). These bias toward caution over
speed; use judgment on trivial tasks.

**Think before coding.** State assumptions explicitly and ask when uncertain.
If a request has several readings, present them instead of silently picking
one. If a simpler approach exists, say so — push back when warranted. If
something is unclear, stop and name what's confusing. This repo already
institutionalizes that habit: non-trivial choices land as an ADR in
`docs/decisions/`, and the blueprint's "honest test" says to cut a planned task
that doesn't improve the output and record why.

**Simplicity first.** The minimum code that solves the problem, nothing
speculative — no abstractions for single-use code, no unrequested
configurability, no error handling for impossible states. `plans/00-overview.md`
§3.1 bans un-earned complexity by name (no broker, no worker fleet). If you
wrote 200 lines and it could be 50, rewrite it.

**Surgical changes.** Touch only what the request requires. Don't "improve"
adjacent code, comments, or formatting, and match surrounding style even where
you'd do it differently. Remove imports and helpers that *your* change
orphaned; mention pre-existing dead code rather than deleting it. Every changed
line should trace directly to the request.

**Goal-driven execution.** Turn the task into a verifiable goal before starting
— "fix the bug" → "write a test that reproduces it, then make it pass" — and
for multi-step work state the plan as steps with the check for each. The checks
here are concrete: `make check` before calling anything done, a targeted
`uv run pytest -k ...` while iterating, and `uv run eval-agent --limit N
--skip-judge` for behavior changes the metrics would catch. Save the full
30-question run for when a real baseline is the point.

## Commands

```bash
make lint        # ruff check + ruff format --check on src tests
make typecheck   # mypy --strict (configured over the `fie` package)
make test        # pytest — fully offline, no network, no API keys
make check       # all three

uv run pytest tests/test_graph.py::test_name   # single test
uv run pytest -k retract -q                    # by keyword

uv run fie ask "..." [--verbose] [--json]      # one run over the ingested corpus
uv run fie resume <run-id>                     # resume a killed run from its checkpoint
uv run fie ingest [--tickers TSLA] [--years 1] [--embedding openai] [--skip-embed]
uv run fie export-runs [--out recorded] [--run-ids ...]  # finished runs → static JSON (ADR 0027)

uvicorn --factory fie.api.app:create_app --port 8000   # the service; refuses to boot without API_KEY

uv run eval-retrieval --embedding both --rerank both   # retrieval benchmark (ADR 0011)
uv run eval-retrieval --figures --rewrite all          # figure delivery + rewrite arms (ADR 0020/0022)
uv run eval-agent [--limit 3] [--skip-judge] [--write-baseline]  # gold set → §6 metrics + gate
uv run eval-adversarial [--rebuild] [--limit N]        # 16 attack cases, poisoned collection
uv run eval-seed                                       # regenerate derived eval sets

make langfuse-up / langfuse-down   # whole dev stack (Langfuse + Qdrant)
make qdrant-up                     # Qdrant only — enough for ask/eval

./deploy/deploy.sh          # on the VPS: decrypt secrets → pull → up -d → smoke test
./deploy/monthly-eval.sh    # the full eval, in a one-off container off the deployed image
./deploy/backup.sh          # tar fie_api_data, 7-day retention (~16 MB steady state)
./scripts/burst-test.sh     # nginx + fail2ban limits, against the live host only
```

`./deploy/monthly-eval.sh` is also **the way to run a full eval on the VPS by
hand** — there is no `uv` on that box and there is deliberately not going to
be. `docs/deploy.md` carries the rest of the procedure, including corpus
seeding and a `crontab -` pipe that installs an empty crontab without saying
so.

Langfuse UI is on **http://localhost:3001** (deliberately remapped off 3000).
Qdrant on 6333. **Production tracing is Langfuse
Cloud, not this stack** — six containers do not fit on the VPS (ADR 0032).

`uv run eval-agent` (30 questions) takes ~75 minutes and costs real money —
never launch it speculatively. Use `--limit N --skip-judge` for smoke checks.

`--write-baseline` is gated since 2026-07-27 (ADR 0015): it refuses a run
that misses the §13 `TARGETS` or dropped questions unless `--force`, and
records `n_questions`/`n_completed` in `eval/baselines.json`. `TARGETS` is
settled and, since ADR 0043, **is the whole gate on every scored metric** —
`numeric_fidelity` 0.85 (same ADR), `expected_fact_coverage` 0.65 (ADR 0020).
Both sit far under the measured 1.000 and **are not worth raising**: every
candidate raise checked on 2026-08-13 sat above a reading that had already
happened.

## Architecture

### The run: one LangGraph state machine

```
START → decompose ═(Send per open sub-question)═> research (parallel)
             ↑                                        │
             │ (critic: mis-split, bounded)           ▼
          critic ←─ retract ←─┬─ verify ←── synthesize (fan-in)
             │                │    ║
             ├→ END           │    ╚═(Send per failed claim — targeted loop-back)
             └→ synthesize ───┘
```

Everything is in `src/fie/graph/build.py` — nodes are closures over injected
dependencies (`build_graph(retriever=..., reconciler=..., tracker=..., ...)`),
so tests run the real graph with fakes and implementations swap without
touching the graph. Three cycles exist (verify→research, critic→synthesize,
critic→decompose) and all terminate via iteration caps enforced in the
**conditional edges**, not inside nodes.

`src/fie/runner.py` is the single production wiring point — the CLI and
`eval-agent` both go through `answer_question()`, so the eval measures exactly
the system users run.

### State and reducer channels

`src/fie/state/__init__.py` holds every Pydantic model plus `EngineState` (the
graph schema) and `build_run_result()` (pure derivation of the output
contract). Channels written concurrently by parallel research branches
(`evidence`, `branch_results`, `run_budget`, `error`) are `Annotated` with
reducers so branch updates merge instead of clobber. **Branches never write
`sub_questions` directly** — they report on `branch_results` and the fan-in
stamps statuses. Adding a channel that branches write requires a reducer.

### Verification: three claim types, three paths

`verify` routes by `claim_type`:
- **numeric** → `XbrlNumericReconciler` (`tools/reconcile.py`): a cheap model
  maps claim text → candidate XBRL `(concept, period, unit)`, then a
  deterministic SQLite lookup grounds it only on an exact match at the claim's
  stated precision. No LLM verdict. Table-chunk cell matching is the fallback
  (ADR 0012).
  A `numeric` claim whose only numbers are days of the month is an event, not
  a figure, and goes to entailment instead (ADR 0047). The reconciler could
  only match its day.
- **qualitative** → entailment judge (LLM).
- **forward_looking** → attribution judge (LLM).

Numeric truth lives in the XBRL facts store, never in parsed HTML cells —
those are display provenance only.

### Retraction is load-bearing

`retract` excises still-unsupported claims from the prose. When excision
*fails* to locate the sentence, the claim must stay `unsupported` (not
`retracted`) so it still counts against `hallucination_rate` — marking it
retracted hides surviving hallucinated text from the exit-gate metric. Every
`hallucination_rate: 0.000` measured before 2026-07-26 is an artifact of that
bug and is not trustworthy.

Since 2026-07-31 the claim → sentence mapping is captured **at synthesis**,
not guessed back afterwards: `DraftClaim.answer_span` quotes the sentence
verbatim, is validated as a substring of the answer, and is repaired by one
bounded retry when it is not (ADR 0023). Excision deletes that span — exact,
idempotent when two claims quote one sentence, and incapable of taking a
sentence from another claim. Token overlap survives only as a fallback for a
claim carrying no span, and its use is logged. Before this, per-claim overlap
matching over a *mutating* draft deleted 26 of 204 answer sentences across
one run, including three that carried `grounded` claims still advertised in
the grounding table (ADR 0021).

**Two `hallucination_rate: 0.000` readings now exist and mean opposite
things.** The pre-2026-07-26 one hid surviving hallucinated text. The
post-ADR-0021 one is real — the four claims it used to count were already
gone from the prose. The distinguishing evidence is the pre/post-retract diff
and the invariant test in `tests/test_graph.py`; a 0.000 with no invariant
asserted is suspect. The accepted 2026-08-01 baseline was checked that way
before it was accepted: 8 claims retracted, every one carrying an
`answer_span`, **0** of those spans still in the delivered prose, and no
scored row with verdict `unsupported` or `None` in 217. Re-check any future
0.000 the same way rather than trusting it.

Point 3 of ADR 0021 — dropping a `grounded` claim whose sentence another
claim's excision removed — was predicted to go extinct under ADR 0023 and
**did not**. It fires when one sentence carries both a retracted and a
grounded claim, which spans cannot prevent. See the correction in that ADR.

### The facts store on both sides of the graph

`plans/00-overview.md` §3.3 put numeric truth in XBRL, and until 2026-07-31
only the verification half existed — the system could **check** a number it
could not **look up**. `tools/fact_lookup.py` closes that: `research` maps its
query to filed figures and injects them as evidence carrying their own
accession, so a claim citing one keeps the same citation trail as a claim
citing prose (ADR 0022). Three things about it are load-bearing, not
incidental: injected chunks outrank retrieved ones in the fan-in packing sort
(the cap silently dropped two figures retrieval *had* found), their
`chunk_type` is `xbrl` rather than `table` so the reconciler's table fallback
cannot make the store corroborate itself, and a year-scoped balance resolves
to the year's closing date. Injection is additive — an empty mapping or a
model failure leaves the branch exactly as it was.

`research` also rewrites its query into filing vocabulary before retrieving
(one call, no fan-out, no new graph node — it is a per-branch step with no
fan-in). Measured on the figure labels, that moves recall@8 from 0.354 to
0.451. The `{raw, rewritten}` union was measured and **rejected**: worse at
every depth. The query actually searched is recorded on `branch_results`, so a
retrieval failure stays distinguishable from a rewrite failure.

### Model routing

`src/fie/llm/get_model(role)` is the **only** way to obtain a chat model. Model
IDs are never written at call sites — each role resolves to a `ModelSpec` in
`Settings.models` (`src/fie/config.py`), whose `provider` picks the client
path. Anthropic goes through `langchain-anthropic` (native SDK, adaptive
thinking); everything else through OpenRouter's OpenAI-compatible endpoint. An
Anthropic model routed through OpenRouter raises (ADR 0014). Every model is
tagged `fie-role:<role>` so Langfuse shows which role made each call.

`bind_structured()` picks the structured-output method by provider and this
matters: OpenRouter → `function_calling` (some open models fence `json_schema`
output in markdown); Anthropic **with thinking on** → `json_schema` (the
default forced-tool-calling path is explicitly not guaranteed under thinking).

`synthesis` and `critic` run on GLM 5.2 (`z-ai/glm-5.2-20260616`) — the accepted
default since the cost A/B closed on it (ADR 0045): it held the gold-set floors
at a fraction of the Anthropic price. The planned Sonnet synthesis experiment is
dropped. `eval_judge` stays on a strong stable model: it is the measuring
stick. `fact_lookup` (question → the figures to look up) and `query_rewrite`
(analyst phrasing → filing vocabulary) are their own cheap roles rather than
reuses of `fact_mapping`, so a Langfuse trace separates what research looked
up from what verify checked.

### Retrieval and ingestion

`tools/retrieval/` implements the `Retriever` protocol (`retrieve(query, k)`)
that the graph sees. Production is `QdrantHybridRetriever`: named dense +
sparse (BM25) vectors, RRF-fused server-side, optionally cross-encoder
reranked. Embedding provider and reranker are **decided by `eval-retrieval`
measurement, not taste** — ADR 0011 holds the numbers (currently
`openai`/`text-embedding-3-small`, reranker `none`). One Qdrant collection per
embedding model so arms sit side by side.

`ingest/pipeline.py` is resumable in three per-filing stages tracked in the
SQLite manifest: **acquire** (network → `data/parsed/`) → **chunk** (local →
`data/chunks/`) → **embed** (API + Qdrant). Re-running skips completed stages;
failures isolate to their filing. `--skip-embed` runs the free stages with no
API keys.

### The service, and what actually protects it

`src/fie/api/` wraps `answer_question` in HTTP. `create_app` injects its
dependencies the way `build_graph` does, so the whole surface — including the
abuse controls that guard real money — runs against a fake engine in
`make test` with no network, no keys and no corpus.

**Two sentences here are load-bearing and must not be softened.**

**On the public path the API key identifies; it does not control** (ADR 0030).
The Phase 5 frontend is a static SPA and ships its key in the bundle.
Turnstile — a token minted per visitor, per challenge — is what holds that
path, together with a USD spend governor, a per-IP daily quota and nginx.
`X-API-Key` authenticates the tailnet consumer and nothing else.
`test_turnstile_refuses_a_submission_carrying_a_valid_api_key` is that
sentence as an assertion.

**An accepted claim is `accepted`, never `grounded`** (ADR 0034). That is what
stops the human review gate from laundering an opinion into `grounding_rate`.
The gate is off by default and off in the eval, so it can never move a metric.

The rest, briefly: the job model is in-process asyncio with a semaphore (the
OOM control) and a queue cap (the honesty control) — no broker, per §3.1 and
ADR 0026. Serving state is `data/api.db`, separate from `ingest.db` and
`checkpoints.db`. Progress comes from the checkpoint, not the job row. The
answer cache is **exact-match only** and is checked *before* the governor, so
a cached answer costs nothing and spends no quota. Per-IP identity is
`X-Real-IP`; `X-Forwarded-For` is caller-controlled and is not an identity.
`fie export-runs` emits the exact `GET /v1/jobs/{id}/result` payload as static
JSON, and `recorded/` holds the accepted baseline's 30 runs — the CDN answers
the visitor who only reads, so the VPS never sees the request (ADR 0027).

Deployment: `Dockerfile` + `docker-compose.prod.yml` + `deploy/`. CI runs
ruff/mypy/pytest and publishes the image; **it holds no secret at all**, and
`grep -r 'secrets\.' .github/` returning only `GITHUB_TOKEN` is the gate
(ADR 0037). `docs/security.md` and `docs/monitoring.md` are the operational
view; `docs/deploy.md` is the procedure, written from the first real deploy
rather than from the plan.

### What deploying found, and the tests could not

**The image carries code, not data, and the corpus cannot be rebuilt on the
box.** `run_ingest` always calls the acquire stage, which raises without
`EDGAR_IDENTITY` — personal data deliberately kept off the VPS. So `ingest.db`
(13 MB) and a Qdrant snapshot (71 MB) travel from the machine that ingested;
dev and prod are pinned to Qdrant `v1.18.0` so a snapshot restores exactly.
This step exists in no plan and the deploy is useless without it.

**`/healthz` reported `facts_store: true` over an empty store.** SQLite creates
a database on connect, so the app made a 0-byte `ingest.db` and `path.exists()`
said yes — the service advertising ready for the one thing it could not do,
with every numeric claim failing and the monitor green. `_facts_store_ok()`
reads a row instead. **`path.exists()` is never the question for a SQLite
file**; suspect this check first if numbers fail while health is green.

**Compose bind-mounts preserve host ownership**, so root-owned 0400 secrets are
unreadable to the container's uid 10001 and the app dies naming the *field*,
not the cause. `decrypt-secrets.sh` chowns. Relatedly, `cap_drop: ALL` removes
CAP_CHOWN, so a container cannot fix ownership inside its own volume even as
root — that is a host-side operation at the volume mountpoint.

**The monthly eval runs in a one-off container, not on the host** (ADR 0036,
amended the day it was written). The host has no `uv`, no `data/` and no route
to Qdrant, which publishes no port. Fixing that host-side needed a second copy
of the corpus, and **a second corpus is wrong rather than merely wasteful** —
re-seed one and the eval reports drift that is really a stale snapshot. The
container uses the deployed image and the service's own volume, so the drift
run measures the artifact that is actually serving.

**`backup.sh` archives `fie_api_data` only.** Qdrant's volume was 48 of the
first night's 50 MB and is a snapshot of the developer's collection; a local
backup only survives failures where that original is fine. What cannot be
recreated — `checkpoints.db` and the spend ledger in `api.db` — is 2.2 MB a
night.

### Everything else

- `context/` owns prompt construction and packing — what enters each LLM
  window is decided here, never inside a graph node.
- `guardrails/` — per-run token/cost budget (`BudgetTracker` is a LangChain
  callback), per-node timeout, retry-with-backoff. `guarded()` wraps every
  node; a budget abort sets `error` and edges route to END with a partial
  result.
- `observability/` is the only module that imports Langfuse.
- `eval/` (package) holds metric implementations and runners; `eval/`
  (repo root) holds the versioned datasets, `baselines.json`, and reports.

## Conventions

- Python 3.12, `uv` for everything (`uv run ...`, never bare `python`/`pip`).
  `mypy --strict` and ruff (E,F,W,I,UP,B,SIM,RUF; line length 100) must be
  green.
- Tests never touch the network or need keys — use the fakes in `tests/fakes.py`
  (`StructuredFakeModel` queues structured outputs and can inject transient
  failures/delays to exercise guardrails). `pytest-asyncio` is in auto mode, so
  `async def test_...` needs no decorator.
- Every non-trivial decision gets an ADR in `docs/decisions/` when it is made.
- All env/config lives in `src/fie/config.py`; nothing reads `os.environ`
  directly.
- `EDGAR_IDENTITY` must be set in `.env` and never appear in the repo — SEC
  requires a real name + email in the user agent and 403s noreply addresses.
  The user's personal data has been scrubbed from history once; keep it out.
- Docstrings here carry design rationale, not restated code. Match that when
  editing — explain why a decision holds, and reference the ADR.
- Sequencing rule: never advance a phase while the previous phase's eval
  numbers have regressed. From Phase 3 on, `uv run eval-agent` is the gate.
