# ADR 0011 — Qdrant hybrid retrieval: named vectors, RRF, hosted reranker

**Date:** 2026-07-19 (numbers re-measured 2026-07-26) · **Phase:** 3 · **Status:** accepted — embedding + reranker choices confirmed on verified labels

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
(report `eval/reports/retrieval-20260726-205055.md`):

| variant | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| voyage+rerank | 0.620 | 0.730 | 0.820 | 0.574 | 0.509 |
| voyage+rrf-only | 0.620 | 0.730 | 0.820 | 0.577 | 0.509 |
| openai+rerank | 0.620 | **0.740** | **0.860** | 0.569 | 0.527 |
| openai+rrf-only | 0.620 | **0.740** | **0.860** | **0.579** | **0.528** |

**Status of the numbers:** measured on the full corpus against all 50
**verified** labels (`label_status: verified`). They replace the earlier
seed-label run (`retrieval-20260719-190054.md`), which read ~0.02–0.04
higher on every recall metric — verification narrowed three 10-Q queries
(rq-013/028/040) whose empty `section` matched *any* chunk in the filing and
so inflated recall. Every ranking in the table survived the change; only the
levels moved. nDCG@10 is normalized against the perfectly reordered
retrieved list (section-level labels make several chunks relevant at once).

### Embedding: `text-embedding-3-small` (OpenAI)

recall@5 is identical (0.620), but OpenAI leads everywhere else: deeper
recall (recall@10 0.740 vs 0.730, recall@20 0.860 vs 0.820) *and* top-rank
ordering (MRR 0.579 vs 0.577, nDCG@10 0.528 vs 0.509). On the earlier seed
labels this was a genuine trade-off — Voyage led MRR/nDCG while OpenAI led
recall, and the call rested on the argument that recall matters more for a
RAG feeder. Verification dissolved the trade-off: Voyage's top-rank edge was
an artifact of the three over-broad 10-Q labels, and OpenAI now wins on
every metric. The decision no longer depends on that judgment call.

The finance-tuned Voyage embeddings did **not** beat the general baseline on
this corpus — a genuine finding, not a default assumption. OpenAI is also
the already-live provider, so this keeps the default where the corpus is.

### Reranker: **off** (RRF order)

`rerank-2.5` earns nothing here — on verified labels it is marginally
*negative*. Recall is untouched on both embeddings (a reorder of the top-50
cannot change what is in the candidate set), and the ordering metrics move
the wrong way: on OpenAI, MRR 0.569 with rerank vs 0.579 without and nDCG@10
0.527 vs 0.528; on Voyage, MRR 0.574 vs 0.577. The seed-label run had
already shown it adding ≤0.010; verification flipped that small positive to
a small negative. Either way it is noise, and noise is not worth a
per-query API call and added latency on every production question. Default
the reranker to `none`. Because OpenAI (not Voyage) is the embedding pick, the
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
