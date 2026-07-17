# Phase 3 — Production RAG + Evaluation

**Goal (blueprint §13):** structure-aware ingestion (tables!), the XBRL facts store, Qdrant hybrid retrieval with reranking, the gold eval sets, and the §6 metrics. *Exit with measurable grounding/hallucination numbers.* This is the largest phase; it has three workstreams (A: ingestion, B: retrieval, C: evaluation) that can be interleaved, but C's retrieval eval set must exist before B's embedding/reranker choices are finalized — those are decided by measurement.

## Context (read this if starting cold)

Phases 0–2 delivered the full agent topology over a naive corpus: decompose → parallel research (BM25 over Apple filings, in-memory) → fan-in synthesis → per-claim verification (interim string-match numeric reconciliation) → bounded loop-back → critic, with typed state, checkpointing, guardrails, and Langfuse tracing. Retrieval was built against a protocol interface (`src/fie/tools/`) and numeric reconciliation against a `NumericReconciler` protocol *specifically so this phase can swap both implementations without touching the graph*.

Decisions already made (see `00-overview.md` §3): corpus = **TSLA, AAPL, NVDA**, EDGAR-only (transcripts deferred); numeric ground truth = **XBRL facts**, not parsed HTML cells; embeddings = benchmark `voyage-finance-2` vs `text-embedding-3-small` on the retrieval eval set; store = **Qdrant**.

## Prerequisites

- Phase 2 exit gate passed. Docker running (Qdrant joins `docker-compose.dev.yml`). API keys added: `VOYAGE_API_KEY`, `OPENAI_API_KEY` (embeddings baseline only).

## Workstream A — Ingestion (`src/fie/ingest/`)

**Corpus scope:** per company — last **3 fiscal years**: 3 × 10-K, ~8–9 × 10-Q, and 8-Ks filtered to earnings releases (Item 2.02 + EX-99 press releases). ~40–50 filings total. Scope is config, not code.

1. **Fetcher:** extend the Phase 0 `edgartools` path into a batch pipeline: enumerate filings per ticker/form/date-range, download, cache raw under `data/raw/` (gitignored), with a manifest (accession number, form, filing date, fiscal period, checksum) in SQLite. Idempotent — re-running skips cached filings. Respect SEC rate limits (edgartools handles this; verify).
2. **Structure-aware parsing (the hard part, blueprint §5):**
   - Narrative: `edgartools` item/section accessors → sections with stable identifiers (e.g. `10-K/Item 7 MD&A`). Fallback if sections come out too coarse or mangled (test on TSLA — its filings are the least standard of the three): `sec-parser` for element-level HTML structure. Decide on evidence, record the ADR.
   - **Tables:** never flattened to prose. Each table becomes its own chunk: a markdown/text rendering for embedding + the structured original (rows/cols/headers) in the chunk payload, plus caption and containing section. Cell provenance for *display*; numeric *correctness* comes from XBRL (below).
3. **Chunking:** section- and table-aware (blueprint §5 — not fixed-window). Narrative sections split at paragraph boundaries into ~512–1024-token chunks with ~15% overlap; tables atomic (split only by row-groups if oversized). Every chunk carries the full citation trail: `ticker, form_type, accession_no, filing_date, fiscal_period, item, section_title, chunk_type (text|table), anchor`. Extend Phase 0's `EvidenceChunk` metadata; do not fork a new type.
4. **XBRL facts store (decision 3.3):** for each filing, pull XBRL facts via `edgartools` into SQLite: `facts(ticker, concept, label, value, unit, decimals, period_start, period_end, fiscal_period, accession_no, form_type)`. Normalize units/scale on ingest. This table is the numeric ground truth for verification **and** for gold-answer construction in Workstream C.
5. **Pipeline CLI:** `fie ingest --tickers TSLA,AAPL,NVDA --years 3` — fetch → parse → chunk → embed → upsert to Qdrant + facts to SQLite, resumable, with a summary report (filings, chunks, tables, facts, failures).

## Workstream B — Retrieval (`src/fie/tools/retrieval/`)

1. **Qdrant** (docker, dev): one collection, **named vectors**: `dense` (embedding model per benchmark) + `sparse` (BM25 sparse vectors via `fastembed`). Full chunk metadata in payload; payload indexes on `ticker`, `form_type`, `fiscal_period`, `chunk_type` for filtered retrieval (research agents should filter by company/period when the sub-question implies it).
2. **Hybrid retrieval:** Qdrant Query API — prefetch dense + sparse, fuse with RRF, top-50 → **rerank** → top-k (default 8) to the agent. Reranker: hosted cross-encoder (`voyage rerank-2.5` if Voyage wins embeddings — one vendor; else Cohere rerank) with a local `bge-reranker-v2-m3` fallback documented. Chosen on the retrieval eval, like the embeddings.
3. **Swap-in:** implement the Phase 0 retrieval protocol; delete the BM25 placeholder; research nodes unchanged. Justify hybrid + reranking in the README (blueprint §5 — a retrieval reviewer reads this closely).
4. **Exact numeric reconciliation (`src/fie/tools/reconcile.py`):** replace the Phase 1 interim reconciler: cheap-model maps claim text → candidate XBRL `(concept, period, unit)` triples → deterministic SQLite lookup → exact match after scale normalization = `grounded` (supporting span = the fact row, cited by accession number); no match → try table-chunk cell match as fallback → else `unsupported` with feedback naming what was searched.

## Workstream C — Evaluation (`eval/`)

Two separate eval layers, attributing failures to retrieval vs reasoning (blueprint §5/§6):

1. **Retrieval eval** (build FIRST): ~50 queries with human-labeled relevant chunks/sections (seeded by generating candidates per section with a cheap model, then hand-verifying — the operator reviews). Metrics: recall@k (k=5,10,20), MRR, nDCG@10. Runner: `uv run eval-retrieval` → JSON + markdown report. **Use it to run the embedding benchmark (voyage-finance-2 vs text-embedding-3-small) and the reranker on/off comparison; record both ADRs with the measured numbers.**
2. **End-to-end gold set:** ~30 analyst questions across the 3 companies (mix: single-company numeric, multi-period trend, cross-company compare, risk/qualitative, and 3–5 *unanswerable-from-corpus* questions — the system must retract/decline, not invent). Each: question, human-checked expected key facts (numeric ones pulled from the XBRL store), expected source documents. Stored as versioned JSONL.
3. **Metrics (blueprint §6), implemented in `eval/metrics.py`:**
   - **Grounding rate:** % claims in final answers with a `grounded` verdict and a cited source.
   - **Hallucination rate:** % claims with no valid support that *survive* to the final answer (retracted claims don't count against — retraction is the system working).
   - **Citation accuracy:** does the cited span support the claim — judged by a `claude-opus-4-8` LLM-judge against the span, spot-checked by hand on 20%.
   - **Numeric fidelity:** % of figures in final answers exactly reconciling to an XBRL fact.
   - **Verifier precision/recall:** a labeled claim set (~100 claims: real claims + systematically perturbed ones — digits changed, periods shifted, sentiment inverted) with true verdicts; measures the verifier itself, per claim type. **This is also the measurement that decides verifier model escalation (decision 3.5).**
4. **Regression runner:** `uv run eval-agent` runs the gold set end-to-end → metrics JSON + markdown report + Langfuse-linked traces; `eval/baselines.json` holds the accepted numbers; the runner exits non-zero if any metric drops below baseline. This becomes the CI gate in Phase 4 and the phase-advance gate from now on (blueprint §13 sequencing rule).

## Explicitly out of scope

Transcripts (deferred, `00-overview.md` §2). FastAPI/deploy (P4). Long-term memory (blueprint §11).

## Exit gate

- `fie ingest` populates Qdrant + facts store for all 3 companies; summary report clean.
- Retrieval eval report exists with the embedding + reranker decisions recorded as measured ADRs.
- `uv run eval-agent` produces the first baseline: grounding rate, hallucination rate, citation accuracy, numeric fidelity, verifier P/R (per claim type) — all committed to `eval/baselines.json`. Targets to beat before Phase 4: hallucination rate ≤ 2%, numeric fidelity ≥ 95%, grounding rate ≥ 90%; if unmet, iterate here — do not advance (blueprint §13).
- Verifier routing decision (cheap vs frontier per claim type) made from the measured P/R and recorded.
