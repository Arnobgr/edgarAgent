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

**What is open is the public path, and it is all Phase 5.** No nginx vhost, no
Turnstile widget (the widget validates the hostname it rendered on, so it
cannot exist before Cloudflare Pages does), `FIE_TURNSTILE_REQUIRED=false`, and
the uptime prober paused. Two exit-gate items wait on that vhost:
`scripts/burst-test.sh` and one full `eval-agent` on the box. **Deferring the
full run was a decision, not an oversight** — the corpus is provably identical,
Phase 4 changed no part of the answer path, and the box's 1-CPU/640 MB cap
plausibly moves only `degraded_attempts`, which the 1 September cron will
settle. Run it early if anything touches retrieval, prompts, models or the
corpus first. `eval/baselines.json` holds run `agent-20260801-122527`, which
passed 30/30:

| | **2026-08-01** | target |
|---|---|---|
| `grounding_rate` | **0.972** | ≥ 0.90 |
| `hallucination_rate` | **0.000** | ≤ 0.02 |
| `numeric_fidelity` | **0.963** | ≥ 0.85 |
| `expected_fact_coverage` | **1.000** | ≥ 0.65 |
| `citation_accuracy` | **0.851** | not gated |

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

**`citation_accuracy` does not gate.** It is written to `baselines.json` but
is absent from `TARGETS`, so it never refuses a run. Do not re-derive the
diagnosis of why it moved: the change was in the *claims*, not the citations
(ADR 0024, commit `dcef747`). The judge flips on about 1 in 27 byte-identical
spans, so a single reading carries ±0.037.

**`hallucination_rate: 0.000` has two possible meanings** — see "Retraction is
load-bearing".

**An answer with no claims passes every gate** (ADR 0038, *proposed, not
implemented*). Run `2b11fa1d4f9e` of the accepted baseline states
`$18,497,000,000` in a 434-character draft carrying **zero** claims in every
checkpoint it ever wrote — nothing to ground, so nothing to fail, and
`n_completed` counted it. That is a hole in the instrument, not a bad answer.

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
settled — `numeric_fidelity` 0.85 (same ADR), `expected_fact_coverage` 0.65
(ADR 0020), now a floor well under the measured 1.000 and worth raising.

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

Roles currently on the GLM challenger arm for the cost A/B — `synthesis` and
`critic` point at `z-ai/glm-5.2-20260616`; revert both to
`ModelSpec(provider="anthropic", model="claude-sonnet-5")` for the Sonnet
baseline. `eval_judge` stays on a strong stable model: it is the measuring
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
