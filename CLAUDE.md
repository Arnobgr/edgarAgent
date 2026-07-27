# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`fie` — Financial Intelligence Engine: a multi-agent verification system that
answers analyst questions over SEC filings (TSLA/AAPL/NVDA, EDGAR-only) where
every factual claim is verified against a source or explicitly retracted.

The design spec is `financial-intelligence-engine-blueprint.md` (gitignored,
local-only). `plans/00-overview.md` resolves its open decisions and defines
conventions; each phase has a self-contained plan in `plans/`. **Current phase:
3** (production RAG + evaluation). Its exit gate ran on 2026-07-27 and
**failed** all three §13 targets (grounding 0.735, hallucination 0.030,
numeric_fidelity 0.566, n=29), so Phase 4 stays blocked;
`plans/phase-3-exit-gate.md` is terminal and the live entry point is
`plans/phase-3-metric-diagnosis.md`. At least one failing metric is suspected
mis-specified rather than genuinely failing — read that plan before trying to
"fix" retrieval, prompts, or models.

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

uv run eval-retrieval --embedding both --rerank both   # retrieval benchmark (ADR 0011)
uv run eval-agent [--limit 3] [--skip-judge] [--write-baseline]  # gold set → §6 metrics + gate
uv run eval-seed                                       # regenerate derived eval sets

make langfuse-up / langfuse-down   # whole dev stack (Langfuse + Qdrant)
make qdrant-up                     # Qdrant only — enough for ask/eval
```

Langfuse UI is on **http://localhost:3001** (deliberately remapped off 3000;
the README's setup step still says 3000 and is stale). Qdrant on 6333.

`uv run eval-agent` (30 questions) takes ~75 minutes and costs real money —
never launch it speculatively. Use `--limit N --skip-judge` for smoke checks.

**Do not pass `--write-baseline` until the metric work in
`plans/phase-3-metric-diagnosis.md` is settled.** It overwrites
`eval/baselines.json` unconditionally, even when the gate fails
(`agent_runner.py:267-281`); the last run's failing numbers were reverted by
hand to stop them locking in as the Phase 4 CI bar. The file is deliberately
still all-`null`.

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
stick.

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
