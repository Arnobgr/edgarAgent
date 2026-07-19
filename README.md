# Financial Intelligence Engine (fie)

A multi-agent verification system for analyst-grade questions over SEC
filings — every factual claim verified against a source or explicitly
retracted. See `financial-intelligence-engine-blueprint.md` for the design and
`plans/` for the phase-by-phase build.

**Current phase: 3 — production RAG + evaluation.** The Phase 2 topology
(decompose → parallel research → synthesis → per-claim verification →
bounded loop-backs → critic) now runs over a real corpus: TSLA, AAPL and
NVDA filings (last 3 fiscal years of 10-Ks, 10-Qs and earnings 8-Ks),
ingested structure-aware (tables kept structured, never flattened),
retrieved from Qdrant with hybrid search + reranking, and numerically
verified against a local XBRL facts store. Two eval layers (`eval/`)
measure retrieval and the end-to-end §6 metrics; `uv run eval-agent` is
the regression gate from here on.

## Setup

Prereqs: [`uv`](https://docs.astral.sh/uv/), Docker Desktop.

```bash
uv sync

# 1. Start the local stack (Langfuse + Qdrant; first boot takes a minute)
make langfuse-up
# 2. Open http://localhost:3000 → sign up → create org + project → copy API keys
# 3. Configure secrets
cp .env.example .env   # ANTHROPIC_API_KEY, OPENROUTER_API_KEY, LANGFUSE_*,
                       # EDGAR_IDENTITY, VOYAGE_API_KEY (embeddings + rerank),
                       # OPENAI_API_KEY (embedding benchmark baseline only)
# 4. Ingest the corpus (resumable; re-runs skip everything already ingested)
uv run fie ingest
```

## Usage

```bash
uv run fie ask "How has Tesla's automotive gross margin evolved over the last three fiscal years?"
```

Prints the answer with its grounding table (per-claim verdicts, supporting
spans, EDGAR citations by accession number) and the Langfuse trace URL.

```bash
uv run fie ingest --tickers TSLA --years 1 --skip-embed   # partial/offline ingest
uv run eval-retrieval --embedding both --rerank both      # retrieval benchmark
uv run eval-agent                                         # gold set → §6 metrics + baseline gate
uv run eval-seed                                          # regenerate derived eval sets
```

## Retrieval design (why hybrid + reranking)

Retrieval is Qdrant with **named dense + sparse vectors**, fused with RRF,
then reranked by a hosted cross-encoder (`src/fie/tools/retrieval/`):

- **Sparse (BM25) alongside dense.** Filing language is exact-term heavy:
  tickers, item numbers ("Item 7A"), US-GAAP concept names, and literal
  figures. Dense embeddings blur exactly those tokens, while BM25 nails
  them; conversely, analyst phrasing ("capex", "cash burn") rarely matches
  filing phrasing ("purchases of property and equipment") except
  semantically. Each vector type covers the other's blind spot.
- **RRF fusion** combines the two rankings without tuning score
  normalization across incomparable score distributions, server-side in
  one query.
- **Reranking.** The fused top-50 is recall-oriented; the agent reads only
  k=8 chunks, so precision at small k is what matters. A cross-encoder
  reads query and chunk *together* — the measured precision win at k≤10
  that bi-encoders structurally can't provide. Primary: Voyage
  `rerank-2.5` (single vendor with the embeddings if Voyage wins the
  benchmark); documented local fallback: `bge-reranker-v2-m3`
  (self-hosted, no per-call cost, wants a GPU). The rerank on/off delta is
  an arm of `eval-retrieval`, so the stage justifies itself with numbers
  or gets removed.
- **Filtered retrieval.** Payload indexes on ticker/form/period/chunk-type;
  filters are inferred from the query only when it names exactly one
  company or one explicit period — never on ambiguous or cross-company
  questions.

Embedding model (voyage-finance-2 vs text-embedding-3-small) and the
reranker are **decided by the retrieval eval**, not by taste — ADR 0011
holds the measured numbers.

Numeric truth is separate from retrieval: every numeric claim reconciles
against a local **XBRL facts store** (SQLite), exactly, at the claim's
stated precision (ADR 0012). Table chunks keep their structured cells for
display provenance only.

## Development

```bash
make lint        # ruff check + format check
make typecheck   # mypy --strict
make test        # pytest (no network)
make check       # all of the above
```

## Layout

The package mirrors the blueprint's harness layers (`plans/00-overview.md` §5):
`llm/` (role-based model router) · `state/` (typed graph state) · `graph/`
(orchestration) · `context/` (context packing) · `tools/` (retrieval,
reconciliation, legacy fetch) · `ingest/` (EDGAR pipeline, parsing,
chunking, facts store, embeddings) · `eval/` (metrics + runners; datasets
live in the repo-root `eval/`) · `observability/` (Langfuse wiring) ·
`guardrails/` · `api/` (Phase 4 placeholder). Decisions are logged in
`docs/decisions/`.
