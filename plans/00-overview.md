# Implementation Plan — Overview & Master Decisions

**Project:** Financial Intelligence Engine — a multi-agent verification system for analyst-grade questions over SEC filings, with zero hallucinated figures and full citation traceability.
**Source spec:** `../financial-intelligence-engine-blueprint.md` (the blueprint). This document resolves the blueprint's open decisions (§15) and defines conventions shared by every phase. Each phase has its own self-contained plan in this directory.

---

## 1. Phase map

| Phase | Document | Delivers | Exit gate (summary) |
|---|---|---|---|
| 0 | `phase-0-harness-skeleton.md` | Repo scaffold on the harness-layer structure, Langfuse tracing, minimal retrieve→synthesize graph over one 10-K, CLI | One question answered end-to-end, fully traced |
| 1 | `phase-1-cyclic-core.md` | Verifier with per-claim verdicts, verify→research loop-back, guardrails (iteration cap, budgets) | Planted-false-claim test: unsupported claims get caught, re-retrieved or retracted |
| 2 | `phase-2-fan-out.md` | Decomposer, parallel research fan-out, fan-in synthesis | Multi-part question runs with visible parallel branches in the trace |
| 3 | `phase-3-rag-and-eval.md` | EDGAR ingestion (table/structure-aware), XBRL facts store, Qdrant hybrid retrieval + reranking, gold eval sets, metrics, regression runner | Measured grounding/hallucination/citation/numeric-fidelity numbers on the gold set |
| 4 | `phase-4-backend-and-deploy.md` | FastAPI service (async jobs), Docker, VPS deploy, Langfuse migration, CI/CD, secrets, monitoring | Deployed API answers a question; CI runs tests + eval subset; green build |
| 5 | `phase-5-frontend.md` | Vite/React grounding-table UI on Cloudflare Pages | Recruiter-grade demo: color-coded claims, hover-to-source |
| 6 | `phase-6-security.md` | Injection threat model, implemented mitigations, adversarial eval cases, attack-surface writeup | Poisoned-document tests pass; writeup published in repo |
| 7 | `phase-7-cost-and-rigor.md` | Cost/routing decision note with real math, test-coverage pass, README architecture doc | Cost-per-question documented; README complete |
| 8 | `phase-8-mcp-server.md` | MCP server exposing `get_verified_dossier` | Claude Desktop consumes the engine as a tool |

**Sequencing rule (blueprint §13):** never advance a phase while the previous phase's eval numbers have regressed. From Phase 3 onward, the regression runner is the gate; before Phase 3, the gate is the phase's smoke/behavior tests.

## 2. Decisions locked with the operator (2026-07-16)

- **Corpus companies:** Tesla (TSLA), Apple (AAPL), NVIDIA (NVDA).
- **Transcripts:** deferred — v1 is EDGAR-only (10-K, 10-Q, 8-K). Earnings-call transcripts are a named later addition (clean free sources don't exist; revisit when the pipeline is proven). Blueprint text mentioning transcripts is read as "EDGAR corpus" throughout these plans.
- **Langfuse during development:** self-hosted locally via docker compose (Phases 0–3); migrated to the VPS in Phase 4.
- **VPS:** existing VPS, reachable over the operator's Tailscale network from their Mac. The operator provides the auth command when Phase 4 starts. Phase 4 plans deployment onto it, not provisioning from scratch.

## 3. Resolved open decisions (blueprint §15)

Each was to be resolved with a recommendation and reasoning, not picked silently.

### 3.1 Async job model → in-process asyncio + polling, persisted in SQLite
A full run is minutes, not hours, on a single VPS with one consumer (the frontend). The lightest thing that handles it cleanly: `POST /v1/questions` creates a job row in SQLite and starts an `asyncio` task in the FastAPI process; clients poll `GET /v1/jobs/{id}`. No broker, no worker fleet — that would be exactly the un-earned complexity the blueprint bans (§11 names the distributed boundary explicitly). Crash recovery comes from LangGraph checkpointing (3.4), not from a queue. **Upgrade trigger:** multiple uvicorn workers or restart-resilient scheduling → adopt `arq` (Redis) then, not now. Details in Phase 4.

### 3.2 Embedding model → `voyage-finance-2` primary, `text-embedding-3-small` as the cheap baseline, decided by measurement
Voyage's finance-domain model is the best published fit for filing text; OpenAI's `text-embedding-3-small` is the ~$0.02/M-token cheap baseline. Phase 3 builds a retrieval eval set first and benchmarks both on recall@k / nDCG before committing; the corpus is small enough (~tens of filings) that re-embedding to switch costs almost nothing. The decision note records the measured tradeoff — which is itself the "cost/quality tradeoff documented" artifact the blueprint asks for.

### 3.3 Table parsing → XBRL facts as numeric ground truth, `edgartools` for fetch + structure
The key insight: **don't reconcile numbers against parsed HTML cells — reconcile against XBRL.** Every 10-K/10-Q figure that matters is machine-tagged in the filing's XBRL (concept, value, unit, period, accession number). `edgartools` (actively maintained) fetches filings, extracts sections/items, and exposes XBRL facts directly. So: narrative sections → structure-aware text chunks; tables → chunked with structure preserved for retrieval context; **numeric verification → exact lookup in a local XBRL facts store** (SQLite), with unit/scale normalization. HTML cell provenance stops being load-bearing for correctness; it only aids display. Fallback if section extraction proves too coarse: `sec-parser` for finer HTML structure. Details in Phase 3.

### 3.4 State persistence → LangGraph `SqliteSaver` checkpointer from Phase 1
Runs checkpoint after every node into a SQLite file. A crash mid-run is resumable (`graph.invoke(None, config)` with the same `thread_id`), and checkpoints double as a debugging/replay aid alongside Langfuse. Postgres checkpointer only if/when multi-process demands it (it won't in v1).

### 3.5 Verifier model routing → deterministic where possible, cheap by default, escalate by measurement
- **Numeric claims:** the verdict is produced by the exact-match XBRL reconciliation tool — no LLM verdict at all. A cheap model only maps claim-text → candidate concept/period for lookup.
- **Qualitative claims:** cheap OpenRouter model as an entailment judge (claim vs. cited span) by default.
- **Forward-looking claims:** cheap model checks attribution (does the source actually contain the forward-looking statement, attributed as such).
- **Escalation rule:** Phase 3 measures verifier precision/recall per claim type on a labeled claim set; any type below threshold (target ≥0.90 precision, ≥0.85 recall) escalates to the frontier model, and the cost delta is recorded in the Phase 7 note. Measured, not guessed — as §15.5 requires.

## 4. Model routing (reasoning substrate, blueprint §3/§10)

Roles, not hardcoded models — every role is a config slot in `src/fie/llm/`:

| Role | Default model | Access path | Why |
|---|---|---|---|
| `synthesis`, `critic` | `claude-opus-4-8` | Anthropic (native SDK / `langchain-anthropic`), adaptive thinking | High-stakes: the draft answer and the quality gate. $5/$25 per M tokens. `claude-sonnet-5` ($3/$15) is the cost-tuning alternative, evaluated in Phase 7. |
| `decompose`, `claim-extract`, `verify-qualitative`, `verify-forward` | cheap model via OpenRouter (start: a flash-class model, e.g. `google/gemini-2.5-flash`) | OpenRouter OpenAI-compatible endpoint | High-volume mechanical steps; exact pick revisited against the eval set. |
| numeric reconciliation | none (deterministic tool) | — | Exact XBRL lookup. |

Rules: Anthropic models are called through Anthropic's own SDK/integration (never through an OpenAI-compatible shim); OpenRouter models through its endpoint. Model IDs and per-role parameters live in config, so routing experiments are one-line changes and every call is tagged with its role in Langfuse.

## 5. Repository conventions (all phases)

- **Language/tooling:** Python 3.12, `uv` (project + venv + lockfile), `ruff` (lint + format), `mypy --strict` on `src/`, `pytest` + `pytest-asyncio`. Frontend (Phase 5): TypeScript, Vite, React.
- **Layout** — mirrors the harness layers of blueprint §3; keep the separation visible:

```
edgarAgent/
├── pyproject.toml, uv.lock
├── docker-compose.dev.yml        # Langfuse stack (P0), Qdrant (P3)
├── src/fie/
│   ├── config.py                 # pydantic-settings; all env/config in one place
│   ├── llm/                      # reasoning substrate: role-based model router, clients
│   ├── state/                    # typed state: Pydantic models + LangGraph state schema
│   ├── graph/                    # orchestrator: nodes/, edges, build_graph()
│   ├── context/                  # context packing: what goes into each call window
│   ├── tools/                    # tool execution: retrieval, reconciliation, fetch
│   ├── ingest/                   # corpus pipeline (P3)
│   ├── observability/            # Langfuse wiring, trace helpers
│   ├── guardrails/               # budgets, timeouts, iteration caps, sanitization
│   ├── api/                      # FastAPI service (P4)
│   └── cli.py                    # `fie ask ...`
├── eval/                         # gold sets, metric implementations, regression runner (P3)
├── tests/
├── frontend/                     # (P5)
├── docs/
│   ├── decisions/                # ADR-style notes: one file per resolved decision
│   └── security.md               # (P6)
└── plans/                        # these documents
```

- **Decision log:** every non-trivial choice (embedding model, reranker, job model, secrets approach…) gets a short ADR in `docs/decisions/` when it's made — this feeds the README and interview story for free.
- **Secrets:** `.env` locally (gitignored), never committed; production approach defined in Phase 4.
- **The honest test (blueprint §0)** applies to plan execution too: if a task in a phase doc turns out not to improve the output, cut it and note why.

## 6. Cross-phase definition of done

A phase is done when: (1) its exit-gate criteria in its own document pass; (2) `ruff`, `mypy`, and `pytest` are green; (3) from Phase 3 on, the eval regression runner shows no metric below its recorded baseline; (4) new decisions are logged in `docs/decisions/`; (5) every new graph node/tool is visible in a Langfuse trace.
