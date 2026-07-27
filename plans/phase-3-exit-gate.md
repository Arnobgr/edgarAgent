# Phase 3 — Exit-gate checklist (manual steps for the operator)

---

## ▶ PHASE 3 IS COMPLETE — the gate FAILED. Go to the diagnosis plan.

**This plan has no remaining work. Do not run anything here.**
**Next session starts at `plans/phase-3-metric-diagnosis.md`.**

The 30-Q baseline ran 2026-07-27 (report `eval/reports/agent-20260727-093137.json`).
All five steps are done. Step 5's judgment, against the blueprint §13 targets:

| metric | target | measured (n=29) | verdict |
|---|---|---|---|
| grounding_rate | ≥ 0.90 | **0.735** | ❌ FAIL |
| hallucination_rate | ≤ 0.02 | **0.030** | ❌ FAIL |
| numeric_fidelity | ≥ 0.95 | **0.566** | ❌ FAIL |

Supporting: citation_accuracy 0.728 (147 claims judged), expected_fact_coverage
0.400, unanswerable_declined 0.750.

**Do NOT start Phase 4** (blueprint §13). The follow-up is scoped in
`plans/phase-3-metric-diagnosis.md` — at least one of these metrics is
suspected mis-specified rather than genuinely failing, so read that before
trying to "fix" retrieval, prompts, or models.

**`eval/baselines.json` is deliberately still all-`null`.** The run wrote a
baseline; it was **reverted by hand** because `--write-baseline` overwrites
unconditionally even when the gate fails (`agent_runner.py:267-281`), and
committing a failing run would lock the failure in as the Phase 4 CI bar. Do
not re-run `--write-baseline` until the metric work below is settled.

**`hallucination_rate: 0.030` is the first trustworthy reading of that metric.**
Every `0.000` before the 2026-07-26 retraction fix was an artifact. It is now
the *closest* of the three to target — do not read it as a regression.

### Decision 3.5 (verifier escalation): NO escalation needed — settled

| claim type | precision | recall |
|---|---|---|
| numeric | 0.979 | 0.979 |
| qualitative | 1.000 | 1.000 |

No claim type is materially below the others, so no role moves to a stronger
model and **ADR 0013 records "no escalation" with these numbers**. The
important consequence: with verifier precision ~0.98, the failing grounding and
numeric_fidelity are **not** a verification problem. They come from synthesis
or from the metric definitions themselves.

**Do NOT re-do:** label verification (all 80 `verified`), the retrieval
benchmark (re-run 2026-07-26 on verified labels, ADR 0011 updated), the
Opus-vs-GLM comparison (settled — stay on GLM), or this baseline run.

---

Everything in Phase 3 is implemented, tested, and running (commits
`fb69820`, `ffaa27c`). What remains is exactly the work that needs *you*:
a billing toggle, human label verification, and one paid eval run. Do the
steps in order — the benchmark (step 2) is meaningless before the labels
are trustworthy (step 3) only in the sense that final ADR numbers should
come from verified labels; running it earlier for a preview is fine.

## Progress (updated 2026-07-26)

- **Step 1 — Voyage embed: DONE.** Voyage billing enabled; both
  collections resident in Qdrant at 4,181 points each
  (`fie_chunks__voyage-finance-2`, `fie_chunks__text-embedding-3-small`).
- **Step 2 — Benchmark: DONE (provisional, seed labels).** All four
  variants measured (report `eval/reports/retrieval-20260719-190054.md`),
  decisions recorded in ADR 0011. **Winners: OpenAI embedding, reranker
  off** — a near-tie on embedding (OpenAI leads deeper recall, Voyage leads
  top-rank ordering); reranker adds ≈0. Defaults now live in
  `src/fie/config.py` (`embedding_provider="openai"`,
  `reranker_provider="none"`); the redundant `.env` overrides were removed.
- **Model switch: DONE.** For this cheaper pass the three Anthropic roles
  (`synthesis`, `critic`, `eval_judge`) are set to `claude-sonnet-5` in
  `config.py` (production default is Opus). Flip back before recording the
  canonical Opus baseline — it's a one-line change.
- **Provider-agnostic router: DONE (ADR 0014).** Roles now resolve to a
  per-role `ModelSpec` (provider + model + optional max_tokens/temperature/
  thinking), so any role can move between Anthropic and OpenRouter with a
  config edit. Branch `feat/router-provider-agnostic` flips `synthesis`/
  `critic` to `z-ai/glm-5.2-20260616` as the cost challenger (judge stays
  Sonnet). A GLM smoke run (`--limit 3 --skip-judge`) passed 3/3 after fixing
  a structured-output fence bug (`bind_structured` forces tool-calling on the
  OpenRouter path).
- **Three-way smoke comparison: RAN, BUT INCONCLUSIVE — blocked by a bug
  (2026-07-23).** Ran the same `eval-agent --skip-judge` smoke under Sonnet,
  GLM 5.2, and Grok 4.5 (`x-ai/grok-4.5-20260708`) at `--limit 3`, then a
  GLM-vs-Sonnet head-to-head at `--limit 10`. Reports:
  Sonnet-3 `agent-20260723-152749`, Grok-3 `153610`, GLM-3 `20260720-213532`,
  GLM-10 `20260723-162646`, Sonnet-10 `20260723-170240`.
  **The comparison is confounded and must NOT be used to pick a model.** At
  `--limit 10` the Sonnet arm threw **10 `OutputParserException`s (one per
  question)** — grounding collapsed to 0.29, hallucination 0.096, fact-coverage
  0.167. The identical GLM run threw **0** and scored grounding 0.51,
  hallucination 0.0. Root cause: on the Anthropic path `bind_structured`
  (`src/fie/llm/__init__.py`) uses forced tool calling, which is unreliable
  when a role has `thinking=True` (the ModelSpec default; synthesis/critic use
  it). OpenRouter already routes around this via `method="function_calling"`.
- **BLOCKER: FIXED 2026-07-26 — but the diagnosis above was wrong.**
  `bind_structured` now uses `method="json_schema"` for Anthropic models with
  adaptive thinking on, removing the combination `langchain-anthropic`
  explicitly warns about while keeping thinking (the reason those roles are on
  Anthropic at all). Verified: warning gone, 117 tests pass, a real
  `eval-agent --limit 2` **with the Sonnet judge enabled** ran clean.

  **However, the claim that this bug *caused* the Sonnet collapse is
  unconfirmed and should not be repeated.** Two findings against it:
  (a) zero `OutputParserException`s reproduced in **36 isolated calls** across
  three strategies (rich evidence, deliberately insufficient evidence, long
  packed context, ±thinking, ±json_schema); (b) `ainvoke_with_retry`
  (`src/fie/guardrails/execution.py:44`) retries **3×** and logs each failure
  with `exc_info`, and all 10 questions reported `n_completed: 10` — so the
  "10 exceptions" were almost certainly logged-then-recovered retries, which
  would not obviously wreck grounding. **Treat the Sonnet-vs-GLM gap as open,
  not explained.** If an Anthropic arm still trails GLM after the fix, that is
  a real quality signal, not plumbing.
- **Step 3 — label verification: NOT STARTED (needs the operator), but a review aid
  is ready.** All 80 labels (50 retrieval + 30 gold) are still
  `label_status: seed`. This gates the *canonical* ADR/baseline numbers;
  re-run step 2 after verifying. A generated cross-check lives at
  **`eval/label_review.md`** (untracked): every label validated against the
  corpus + XBRL store using the real `SectionRef.matches()` rules —
  **0 hard errors, 4 rows worth a glance** (rq-013/028/040 have empty 10-Q
  sections = "matches any chunk", loose but valid → optionally narrow to
  Part I, Item 2 / Item 1; rq-047 Item 8 is correct — footnote topic). Builder
  script: `scratchpad/build_checklist.py`.
- **Retraction bug: FIXED 2026-07-26 — it was hiding hallucinations from the
  exit gate.** `excise_claim` (`src/fie/graph/retract.py:58`) returns the draft
  unchanged when it can't locate a claim's sentence (token overlap below
  `MIN_OVERLAP = 0.5`), but `retract` marked the claim `retracted`
  *unconditionally*. Retracted claims are routed to `RunResult.retracted`
  rather than `.grounding` (`state/__init__.py:295`), and `hallucination_rate`
  iterates only `.grounding` (`eval/metrics.py:93`) — so an unsupported claim
  whose text **survived in the answer prose** became invisible to the metric,
  and the appended section still claimed it "was removed from the answer".
  Observed 4 times in 5 questions on the GLM arm, 1 in 5 on Opus — in runs
  reporting `hallucination_rate: 0.000`. Root cause is structural: the
  synthesis prompt asks for claims "restated as one self-contained sentence"
  (`context/__init__.py:48`), and a self-contained restatement diverges from
  the in-prose sentence by construction. Now a failed excision keeps the claim
  `unsupported` (so it counts against the gate) and the retracted section says
  the text may still appear. Regression test:
  `test_unexcisable_claim_stays_unsupported_and_counts_as_hallucination`
  (verified failing without the fix). **Every `hallucination_rate: 0.000` from
  before this date is unreliable.**
- **Opus 5 vs GLM 5.2 head-to-head (2026-07-26, matched 5 questions, Sonnet
  judge held constant).** Reports: Opus-5 `agent-20260726-212038`, GLM-5
  `agent-20260726-212641`.

  | metric | GLM 5.2 | Opus 5 |
  |---|---|---|
  | grounding_rate | **0.833** | 0.636 |
  | citation_accuracy | **0.667** | 0.381 |
  | numeric_fidelity | 0.531 | **0.569** |
  | expected_fact_coverage | 0.600 | 0.600 |
  | hallucination_rate | 0.000 | 0.000 |
  | claims judged | 15 | 21 |

  The Opus arm threw **zero** `OutputParserException`s — the first clean
  Anthropic synthesis/critic run at this size, confirming the `json_schema`
  fix holds under real load. **Caveat: the comparison is confounded by claim
  count.** Opus 5 emitted 40% more claims, and `grounding_rate` is per-claim,
  so a model that decomposes more is mechanically penalised. Opus 5 is
  documented as producing longer output by default and needing an explicit
  conciseness instruction, which our synthesis prompt does not have. **Verdict:
  stay on GLM on cost** (~8x cheaper on output: $0.95/$2.98 vs $5/$25), not on
  a demonstrated quality win. n=5 — do not over-read either column.
- **Step 4 — agent baseline: DONE (2026-07-27).** Report
  `eval/reports/agent-20260727-093137.json`, GLM arm, Sonnet judge, ~75 min.
  **`n_questions: 30`, `n_completed: 29`** — see the SQLite fault below.
  Numbers in the table at the top. `eval/baselines.json` was written and then
  reverted (see above); the tree is clean.
- **Step 5 — exit-gate judgment: DONE — FAILED on all three targets.** Do not
  start Phase 4. Continue at `plans/phase-3-metric-diagnosis.md`.

### Three findings from the baseline run (carried into the diagnosis plan)

- **BUG — SQLite `SQLITE_MISUSE` cost one question (g-010).** Error text:
  `bad parameter or other API misuse`; the question was logged and **dropped
  from `results`** (`agent_runner.py:163-164`), shrinking the metric
  denominator to 29. Two lifecycle defects are candidates, and the precise
  cause is **unconfirmed** (one occurrence, timing-dependent, not reproduced):
  (a) `runner.py:94` builds an `IngestStore` per question via
  `build_production_reconciler` and **never closes it** — connections leak
  across the run, each `check_same_thread=False` and used from worker threads
  via `asyncio.to_thread`; (b) all questions share one `checkpoints.db` through
  `AsyncSqliteSaver` (`runner.py:105`), where a node timeout or cancellation
  mid-checkpoint-write can leave the connection broken. Sequencing favours (b):
  g-009 hit a DNS failure, then g-010 died instantly at startup.
- **BUG — `expected_fact_coverage` (0.400) silently shrinks its own
  denominator.** Expected facts that fail to resolve hit `continue` **before**
  `total += 1` (`agent_runner.py:114-117`), so a verified label is dropped
  rather than counted as a miss. Five did so in this run: NVDA FY2026
  `us-gaap:Assets`, AAPL FY2025 `CashAndCashEquivalents`, and TSLA/AAPL/NVDA
  `us-gaap:Assets` for g-019. These labels are `verified`, so failing to
  resolve points at concept-term mapping rather than missing data.
- **CORRECTION — the GLM-vs-Sonnet exception asymmetry is dead as evidence.**
  The "10 `OutputParserException`s on Sonnet vs 0 on GLM" confounder recorded
  above is not a model difference. This GLM run threw the same failure (a
  Pydantic `ValidationError` — `SynthesisOutput.claims[25]` came back as an
  empty dict, missing `text` and `claim_type`) and **recovered**:
  `ainvoke_with_retry` (`guardrails/execution.py:48`) retries 3× and logs each
  attempt with `exc_info`. Both halves of the 2026-07-26 suspicion are now
  confirmed — these are logged-then-recovered retries, and **both arms throw
  them**. Do not carry the exception counts forward as evidence for either
  model.

Current state, for orientation:

- Corpus ingested: 78 filings → 4,181 chunks (1,750 tables), 25,937 XBRL
  facts in `data/ingest.db`. Both embedding collections live in Qdrant at
  4,181 points each.
- `.env` no longer carries retrieval overrides — the ADR-0011 winners
  (`embedding=openai`, `reranker=none`) are the `config.py` defaults.
- Provisional numbers (seed labels): see ADR 0011's table for all four
  variants. Chosen variant `openai+rrf-only`: recall@5 0.660, recall@10
  0.760, recall@20 0.880, MRR 0.591, nDCG@10 0.530.

---

## 1. Unlock Voyage and embed the second collection — ✅ DONE

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

## 2. Run the benchmark (embedding + reranker decisions) — ✅ DONE (re-run after step 3)

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

## 3. Hand-verify the eval labels (the "the operator reviews" step) — ✅ DONE (2026-07-26)

All 80 labels are `verified`. Three 10-Q queries were narrowed first
(rq-013/rq-040 → `Item 2`, rq-028 → `Item 1`) because an empty `section`
matched *any* chunk in the filing. The bare `Item N` form is deliberate: the
parser emits both `Item 2` and `Part I, Item 2` **within every filing**, so
`Part I, Item 2` would have missed ~44% of the MD&A chunks. Residual
contamination (`Part II, Item 2`) is ~1% of 10-Q chunks. Benchmark re-run on
the verified labels: `eval/reports/retrieval-20260726-205055.md`, ADR 0011
updated and no longer provisional.

Original instructions, for reference:

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
