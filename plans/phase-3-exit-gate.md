# Phase 3 — Exit-gate checklist (manual steps for the operator)

Everything in Phase 3 is implemented, tested, and running (commits
`fb69820`, `ffaa27c`). What remains is exactly the work that needs *you*:
a billing toggle, human label verification, and one paid eval run. Do the
steps in order — the benchmark (step 2) is meaningless before the labels
are trustworthy (step 3) only in the sense that final ADR numbers should
come from verified labels; running it earlier for a preview is fine.

Current state, for orientation:

- Corpus ingested: 78 filings → 4,181 chunks (1,750 tables), 25,937 XBRL
  facts in `data/ingest.db`. OpenAI collection live in Qdrant
  (`fie_chunks__text-embedding-3-small`, 4,181 points).
- `.env` currently carries `EMBEDDING_PROVIDER=openai` and
  `RERANKER_PROVIDER=none` because the Voyage trial is rate-limited to
  3 RPM / 10K TPM — unusable at corpus scale.
- Provisional numbers (seed labels, openai+rrf-only): recall@5 0.660,
  recall@10 0.760, recall@20 0.880, MRR 0.578, nDCG@10 0.526 (ADR 0011).

---

## 1. Unlock Voyage and embed the second collection

1. Add a payment method at https://dashboard.voyageai.com (Billing).
   Your trial's free tokens still apply — this only lifts the
   3 RPM / 10K TPM cap. The corpus is ~3M tokens; voyage-finance-2 is
   $0.12/1M *after* free tokens run out, so worst case ≈ $0.36.
2. Embed the Voyage collection (resumable; acquire/chunk stages are
   already done and will be skipped):

   ```bash
   uv run fie ingest --embedding voyage
   ```

   Expected report: `embedded: 78 filing(s)`. The embed stage is
   per-collection aware — filings already in the OpenAI collection still
   get embedded into the Voyage one, and re-runs skip whatever the target
   collection already holds. Verify:
   `curl -s localhost:6333/collections/fie_chunks__voyage-finance-2 | jq .result.points_count`
   → 4181.

## 2. Run the benchmark (embedding + reranker decisions)

```bash
uv run eval-retrieval --embedding both --rerank both
```

Four variants: {voyage, openai} × {rerank, rrf-only}. Costs: query
embeddings are negligible; reranking is 50 queries × 50 candidates ×
2 embedding arms on `rerank-2.5` (~3–4M rerank tokens, ≈ $0.10 or free
under trial tokens).

Then record the decisions:

- Paste the result table into `docs/decisions/0011-qdrant-hybrid-retrieval.md`
  and write one paragraph each for the **embedding winner** and the
  **rerank on/off** call, citing the deltas. If Voyage wins, `rerank-2.5`
  stays (single vendor); if OpenAI wins decisively, note that the Cohere
  rerank comparison is the follow-up.
- Set the winners as defaults: remove the `EMBEDDING_PROVIDER` /
  `RERANKER_PROVIDER` override lines from `.env` if Voyage won; if OpenAI
  won, instead change the defaults in `src/fie/config.py`
  (`embedding_provider`, `reranker_provider`) so config matches the ADR.

## 3. Hand-verify the eval labels (the "the operator reviews" step)

- `eval/retrieval_queries.jsonl` — 50 queries. For each: is the query
  something an analyst would ask, and are the labeled
  `(ticker, form_type, section)` refs really where the answer lives?
  Fix/extend `relevant`, then flip `"label_status": "seed"` → `"verified"`.
  Spot-checking against the corpus is easiest via
  `uv run fie ask` or by grepping `data/parsed/*.json`.
- `eval/gold_set.jsonl` — 30 questions. Check the `expected_facts`
  concept/period references resolve to the right fact (values are pulled
  from the store at eval time — verify the *reference*, not a number) and
  that `expected_sources` look right. Flip `label_status` the same way.
- Optional, to grow the query set: `uv run eval-seed` writes
  `eval/retrieval_queries.candidates.jsonl` (cheap-model generated,
  gitignored) — promote any good ones into `retrieval_queries.jsonl` as
  `verified`.
- Re-run step 2 after verification and update ADR 0011's numbers — those
  are the ones that count.

## 4. First end-to-end baseline (paid — ~$15)

Runs all 30 gold questions through the full graph plus the
`claude-opus-4-8` citation judge and the verifier P/R suite:

```bash
uv run eval-agent --write-baseline
```

- My single test question cost ~$0.45 (with loop-backs), so ≈ $15 total.
  A cheap dry run first: `uv run eval-agent --limit 3 --skip-judge`.
- `--write-baseline` writes the accepted numbers into
  `eval/baselines.json`; from then on, plain `uv run eval-agent` exits
  non-zero on any regression (this becomes the Phase 4 CI gate).
- The report (`eval/reports/agent-<ts>.json`) lists
  `citation_spot_check_sample` — hand-check those ~20% of judged
  citations, per blueprint §6.

## 5. Judge the exit gate (blueprint §13: do not advance if unmet)

Targets to beat before Phase 4, from the baseline run:

| metric | target |
|---|---|
| hallucination rate | ≤ 2% |
| numeric fidelity | ≥ 95% |
| grounding rate | ≥ 90% |

Also from the same run: verifier precision/recall per claim type — this
decides **verifier model escalation** (decision 3.5): if a claim type's
precision is materially below the others on the cheap judge, move that
role to a stronger model in `Settings` and record ADR 0013 with the
measured P/R before/after.

If targets are unmet: iterate *here* (retrieval params, prompts, verifier
routing) and re-run `eval-agent` — do not start Phase 4. Commit
`eval/baselines.json` + updated ADRs when the gate passes.
