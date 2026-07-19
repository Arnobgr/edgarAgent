# ADR 0011 — Qdrant hybrid retrieval: named vectors, RRF, hosted reranker

**Date:** 2026-07-19 · **Phase:** 3 · **Status:** accepted (architecture); embedding + reranker choices **pending measurement**

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

## Pending measurement (the eval decides, not taste)

Run once both collections are ingested (`fie ingest --embedding voyage`,
`--embedding openai`) and the query labels are hand-verified:

```
uv run eval-retrieval --embedding both --rerank both
```

- **Embedding:** `voyage-finance-2` vs `text-embedding-3-small` on
  recall@{5,10,20} / MRR / nDCG@10. Record the winner and the measured
  numbers here.
- **Reranker:** rerank on vs off (RRF order). If Voyage wins the embedding
  benchmark, `rerank-2.5` keeps retrieval single-vendor; otherwise
  evaluate Cohere rerank. Local fallback documented in the README:
  `bge-reranker-v2-m3` (self-hostable, no per-call cost, needs GPU for
  acceptable latency) — not shipped as code to keep the dependency set
  lean.

| variant | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| openai+rrf-only (2026-07-19, seed labels) | 0.660 | 0.760 | 0.880 | 0.578 | 0.526 |
| voyage+rrf-only | _blocked: Voyage trial rate limits (3 RPM / 10K TPM) — needs a billing method_ | | | | |
| openai+rerank / voyage+rerank | _blocked: rerank-2.5 under the same trial limits_ | | | | |

**Status of the numbers:** the openai row is measured on the full ingested
corpus (4,181 chunks) but against **unverified seed labels** — treat as
provisional until the operator hand-verifies `eval/retrieval_queries.jsonl`
(`label_status: seed → verified`) and the eval is re-run. The embedding
and reranker *decisions* stay open until the voyage and rerank arms run;
nDCG@10 is normalized against the perfectly reordered retrieved list
(section-level labels make several chunks relevant at once).

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
