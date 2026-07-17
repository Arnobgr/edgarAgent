# Financial Intelligence Engine (fie)

A multi-agent verification system for analyst-grade questions over SEC
filings — every factual claim verified against a source or explicitly
retracted. See `financial-intelligence-engine-blueprint.md` for the design and
`plans/` for the phase-by-phase build.

**Current phase: 0 — harness skeleton.** A minimal LangGraph
(research → synthesize) over Apple's latest 10-K with naive BM25 retrieval,
full Langfuse tracing, and the repo scaffolded on the blueprint's harness
layers.

## Setup

Prereqs: [`uv`](https://docs.astral.sh/uv/), Docker Desktop.

```bash
uv sync

# 1. Start the local Langfuse stack (first boot takes a minute)
make langfuse-up
# 2. Open http://localhost:3000 → sign up → create org + project → copy API keys
# 3. Configure secrets
cp .env.example .env   # fill in ANTHROPIC_API_KEY, OPENROUTER_API_KEY, LANGFUSE_* keys
```

## Usage

```bash
uv run fie ask "What were Apple's main revenue segments last fiscal year?"
```

Prints the answer with inline citations (`[c3]`), the evidence chunks with
their EDGAR provenance, and the Langfuse trace URL.

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
(orchestration) · `context/` (context packing) · `tools/` (fetch, chunking,
retrieval) · `observability/` (Langfuse wiring) · `guardrails/`, `ingest/`,
`api/` (placeholders for later phases). Decisions are logged in
`docs/decisions/`.
