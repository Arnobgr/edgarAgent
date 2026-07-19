# ADR 0011 — Qdrant hybrid retrieval: named vectors, RRF, hosted reranker

**Date:** 2026-07-19 · **Phase:** 3 · **Status:** accepted (architecture); embedding + reranker choices **provisional** — measured on seed labels, to be confirmed on verified labels

## Decision

One Qdrant collection **per embedding model** (`fie_chunks__<model>`), each
point carrying two named vectors:

- `dense` — the embedding under benchmark (cosine).
- `sparse` — BM25 term weights computed locally by fastembed
  (`Qdrant/bm25`), with Qdrant's server-side IDF modifier.

Query path (`QdrantHybridRetriever`): embed the query (`input_type:
"query"` for Voyage) + BM25-encode it → two prefetches (top-50 each,
optionally filtered) → server-side **RRF fusion** → top-50 candidates →
**hosted cross-encoder rerank** → top-k (default 8) to the research agent.

Filtered retrieval: `ticker`, `form_type`, `fiscal_period`, `fiscal_year`,
`chunk_type` are payload-indexed. Filters are inferred from the query
conservatively — applied only when exactly one corpus company, or one
explicit quarter/fiscal year, is named (ambiguous or cross-company queries
search the whole corpus).

## Measurement (the eval decides, not taste)

Both collections ingested (4,181 points each) and benchmarked over the
50-query set with `uv run eval-retrieval --embedding both --rerank both`
(report `eval/reports/retrieval-20260719-190054.md`):

| variant | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| voyage+rerank | 0.660 | 0.750 | 0.840 | **0.608** | **0.537** |
| voyage+rrf-only | 0.660 | 0.750 | 0.840 | 0.598 | **0.537** |
| openai+rerank | 0.660 | **0.760** | **0.880** | 0.591 | 0.530 |
| openai+rrf-only | 0.660 | **0.760** | **0.880** | 0.591 | 0.530 |

**Status of the numbers:** measured on the full corpus but against
**unverified seed labels** — provisional until the operator hand-verifies
`eval/retrieval_queries.jsonl` (`label_status: seed → verified`) and the
eval is re-run. nDCG@10 is normalized against the perfectly reordered
retrieved list (section-level labels make several chunks relevant at once).

### Embedding: `text-embedding-3-small` (OpenAI) — provisional

A near-tie. recall@5 is identical (0.660); OpenAI leads on deeper recall
(recall@10 0.760 vs 0.750, recall@20 0.880 vs 0.840) while Voyage leads on
top-rank ordering (MRR 0.598–0.608 vs 0.591, nDCG@10 0.537 vs 0.530). For a
RAG feeder, deeper recall matters more than fine top-rank ordering — the
synthesizer reads the whole top-k, so getting the relevant chunk *into* the
candidate set (recall) outweighs its exact rank. OpenAI's +0.040 recall@20
edge is the larger effect; its MRR/nDCG deficit is ≤0.017. OpenAI is also
the already-live, proven provider, so this keeps the default where the
corpus is and avoids a switch for no measured gain. The finance-tuned Voyage
embeddings did **not** beat the general baseline on this corpus — a genuine
(if modest) finding, not a default assumption. Revisit on verified labels;
if Voyage's top-rank edge holds and reranking is re-enabled, the call could
flip.

### Reranker: **off** (RRF order) — provisional

`rerank-2.5` earns essentially nothing here. On OpenAI the rerank and
rrf-only rows are **identical** across every metric; on Voyage rerank adds a
single +0.010 to MRR and moves nothing else. A cross-encoder that reorders
the top-50 but changes no recall and ≤0.010 MRR is not worth its per-query
API cost and added latency on every production question. Default the
reranker to `none`. Because OpenAI (not Voyage) is the embedding pick, the
retrieval stack is no longer single-vendor, so the reranker follow-up — if
verified labels later show a real precision gap — is a **Cohere rerank**
comparison, not a return to `rerank-2.5`. Local fallback still documented in
the README: `bge-reranker-v2-m3` (self-hostable, no per-call cost, needs GPU
for acceptable latency) — not shipped as code to keep the dependency set
lean.

**Config:** `embedding_provider="openai"`, `reranker_provider="none"` are set
as the defaults in `src/fie/config.py` to match this ADR, which makes the
`.env` `EMBEDDING_PROVIDER` / `RERANKER_PROVIDER` overrides redundant (they
have been removed). Re-run the benchmark after label verification and update
this section — those are the numbers that count.

## Rationale

- **Hybrid, not dense-only:** filing language is exact-term heavy (tickers,
  concept names, "Item 7A", figures); BM25 catches what embeddings blur,
  dense catches paraphrase ("capex" vs "purchases of property and
  equipment"). RRF needs no tuned score normalization across the two.
- **Rerank stage:** fusion ranks are cheap but coarse; a cross-encoder
  reading query+chunk together is the standard measured-precision win at
  k≤10, and it is the on/off arm the eval quantifies.
- **One collection per model:** embedding dims differ (1024 vs 1536) and a
  fair benchmark needs both corpora resident simultaneously.
