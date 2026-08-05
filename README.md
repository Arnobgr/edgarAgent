# Financial Intelligence Engine (fie)

A multi-agent verification system for analyst-grade questions over SEC
filings — every factual claim verified against a source or explicitly
retracted. See `financial-intelligence-engine-blueprint.md` for the design and
`plans/` for the phase-by-phase build.

**Current phase: 4 — backend and deploy.** The Phase 2 topology (decompose →
parallel research → synthesis → per-claim verification → bounded loop-backs →
critic) runs over a real corpus: TSLA, AAPL and NVDA filings (last 3 fiscal
years of 10-Ks, 10-Qs and earnings 8-Ks), ingested structure-aware (tables
kept structured, never flattened), retrieved from Qdrant with hybrid search +
reranking, and numerically verified against a local XBRL facts store. Two eval
layers (`eval/`) measure retrieval and the end-to-end §6 metrics;
`uv run eval-agent` is the regression gate. Phase 3's exit gate passed 30/30
on 2026-08-01 — `grounding_rate` 0.972, `hallucination_rate` 0.000,
`numeric_fidelity` 0.963, `expected_fact_coverage` 1.000.

Phase 4 wraps that engine in a service: a FastAPI surface with an async job
model, a human-in-the-loop review gate, layered abuse controls, a container,
and a manual deploy behind the VPS's own nginx. **It is deployed and
answering** — on a private path, reachable over the tailnet and nothing else,
with a nightly backup and a monthly drift eval on cron. The public path
(nginx vhost, Turnstile, TLS) is Phase 5: Turnstile validates the hostname its
widget rendered on, and that hostname does not exist yet. `deploy.sh` refuses
to start with Turnstile off *and* a public vhost present, so the two cannot
drift apart. See `docs/deploy.md`, `docs/security.md` and
`docs/monitoring.md`.

## Setup

Prereqs: [`uv`](https://docs.astral.sh/uv/), Docker Desktop.

```bash
uv sync

# 1. Start the local stack (Langfuse + Qdrant; first boot takes a minute)
make langfuse-up
# 2. Open http://localhost:3001 → sign up → create org + project → copy API keys
#    (3001, not 3000 — remapped so the container stops claiming the port every
#    other dev server wants. Production tracing is Langfuse Cloud, ADR 0032.)
# 3. Configure secrets
cp .env.example .env   # ANTHROPIC_API_KEY, OPENROUTER_API_KEY, LANGFUSE_*,
                       # EDGAR_IDENTITY, OPENAI_API_KEY (production embeddings),
                       # VOYAGE_API_KEY (benchmark arm only — ADR 0011)
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
uv run fie resume <run-id>                                # continue a killed run from its checkpoint
uv run fie export-runs                                    # finished runs → static JSON for the demo
uv run eval-retrieval --embedding both --rerank both      # retrieval benchmark
uv run eval-agent                                         # gold set → §6 metrics + baseline gate
uv run eval-seed                                          # regenerate derived eval sets
```

### The service

```bash
uvicorn --factory fie.api.app:create_app --port 8000   # needs API_KEY set
```

`POST /v1/questions` → `202 {job_id}`, poll `GET /v1/jobs/{id}`, fetch
`GET /v1/jobs/{id}/result`. Runs holding claims the machine could not resolve
stop in `awaiting_review`; `GET`/`POST /v1/jobs/{id}/review` lists them and
resumes the graph from its checkpoint (ADR 0034).

**On the public path the API key identifies, it does not control** — a static
SPA cannot hold a secret. Turnstile, a USD spend governor, a per-IP quota and
nginx are the enforcement (ADR 0030, `docs/security.md`).

```bash
docker compose -f docker-compose.prod.yml up -d   # the VPS stack
./deploy/deploy.sh                                # decrypt secrets → pull → up → smoke test
./deploy/monthly-eval.sh                          # the full eval, in a container off the deployed image
./deploy/backup.sh                                # tar fie_api_data, 7-day retention
./scripts/burst-test.sh                           # prove the nginx/fail2ban limits — needs the public vhost
```

The VPS runs no Python toolchain by design, so `monthly-eval.sh` is also how a
full eval is run there by hand. `docs/deploy.md` covers the rest, including the
step no plan had: **the image carries code, not data**, and the corpus cannot
be rebuilt on the box because ingestion needs an `EDGAR_IDENTITY` deliberately
kept off it.

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
- **Reranking — benchmarked, then switched off.** The fused top-50 is
  recall-oriented and the agent reads only k=8, so precision at small k is
  what matters, and a cross-encoder reads query and chunk *together* in a
  way bi-encoders structurally cannot. That argument was still an arm of
  `eval-retrieval` rather than an assumption, and the measured delta was
  ≈0 — so `reranker_provider` is `none` (ADR 0011). The stage was told to
  justify itself with numbers or be removed; it was removed. Voyage
  `rerank-2.5` and a self-hosted `bge-reranker-v2-m3` stay documented as
  the options if that ever changes.
- **Filtered retrieval.** Payload indexes on ticker/form/period/chunk-type;
  filters are inferred from the query only when it names exactly one
  company or one explicit period — never on ambiguous or cross-company
  questions.

Embedding model (voyage-finance-2 vs text-embedding-3-small) and the
reranker are **decided by the retrieval eval**, not by taste — ADR 0011
holds the measured numbers. `text-embedding-3-small` won: roughly tied with
Voyage at the top and ahead on deeper recall, so it is what production
embeds with, one collection per model so the arms stay side by side.

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
`guardrails/` · `api/` (the HTTP service: routes, job registry, SQLite store,
spend governor, Turnstile). Deployment lives in `Dockerfile`,
`docker-compose.prod.yml`, `deploy/` (nginx, fail2ban, deploy/backup/eval
scripts) and `.github/workflows/`. Decisions are logged in `docs/decisions/`;
operations in `docs/security.md` and `docs/monitoring.md`, and the deploy
procedure in `docs/deploy.md` — written from the first real deploy, not from
the plan, and it says which four things that deploy broke.
