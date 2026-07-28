# Evidence: answer recall, and claim staleness in the scored population

**Date:** 2026-07-28 · **Status:** evidence only — **no remedies are proposed
here, by intent** · **Source run:** `eval/reports/agent-20260728-212914.json`
(the accepted Phase 3 baseline)

## Why this document exists

The Phase 3 exit gate passed on 2026-07-28 and `eval/baselines.json` now
holds the accepted baseline. Two issues surfaced *after* that, while reading
the passing run's own artifacts. Neither blocks the gate — both are visible
in the run that passed it.

This document records **only** what was measured, how, and what it puts at
risk. It deliberately contains no fixes, no priorities, and no plan: those
belong to a later session working from this evidence.

Everything below was obtained from artifacts already on disk (checkpoints,
the report, `data/ingest.db`, `data/chunks/`, Qdrant) plus one embedding
call for a retrieval probe. No paid eval run was involved.

## Context a fresh reader needs

The accepted run's metrics:

| metric | value | gate |
|---|---|---|
| n_completed | 30/30 | ✅ |
| grounding_rate | 0.9345 (257/275) | ✅ ≥ 0.90 |
| hallucination_rate | 0.0145 (4/275) | ✅ ≤ 0.02 |
| numeric_fidelity | 0.9256 | ✅ ≥ 0.85 |
| verifier P/R (all) | 1.000 / 1.000 | — |
| citation_accuracy | 0.6848 | not a gate |
| **expected_fact_coverage** | **0.393 (11/28)** | **not a gate** |
| unanswerable_declined | 0.750 | not a gate |

The run produced **312 claims total**; 37 were retracted, leaving **275** in
the population that `grounding_rate` and `hallucination_rate` score.

---

# Issue 1 — The system declines to answer questions whose facts are in the corpus

## What was measured

`expected_fact_coverage` (`src/fie/eval/agent_runner.py`,
`expected_fact_coverage`) asks, for each gold question, whether the XBRL
facts an analyst would expect in the answer actually appear as figures in
the answer text. It reads **11/28 = 0.393**.

Re-running the metric's own logic with per-label reporting:

- **0 of 28 labels fail to resolve in the store.** The ADR 0015 label/query
  fix holds; this is not a label bug and not a denominator artifact.
- **All 17 misses are the same shape:** the fact resolves in
  `data/ingest.db`, and its figure never appears in the answer.

The 17 misses (question, ticker, concept terms, fiscal year, value(s) found
in store):

```
g-001  TSLA  Revenues / total revenue          FY2025  94,827,000,000
g-002  AAPL  NetIncomeLoss                     FY2025  112,010,000,000
g-006  NVDA  us-gaap:Assets / total assets     FY2026  125,254,000,000 / 111,601,000,000
g-008  AAPL  CashAndCashEquivalents            FY2025  30,299,000,000 / 29,943,000,000
g-009  TSLA  Revenues / total revenue          FY2023  96,773,000,000
g-009  TSLA  Revenues / total revenue          FY2024  97,690,000,000
g-009  TSLA  Revenues / total revenue          FY2025  94,827,000,000
g-010  AAPL  ResearchAndDevelopmentExpense     FY2023  29,915,000,000
g-010  AAPL  ResearchAndDevelopmentExpense     FY2024  31,370,000,000
g-010  AAPL  ResearchAndDevelopmentExpense     FY2025  34,550,000,000
g-015  TSLA  Revenues / total revenue          FY2025  94,827,000,000
g-016  TSLA  ResearchAndDevelopmentExpense     FY2025  6,411,000,000
g-016  AAPL  ResearchAndDevelopmentExpense     FY2025  34,550,000,000
g-016  NVDA  ResearchAndDevelopmentExpense     FY2026  18,497,000,000
g-018  AAPL  NetIncomeLoss                     FY2025  112,010,000,000
g-019  AAPL  us-gaap:Assets                    FY2025  344,085,000,000 / 364,980,000,000
g-019  NVDA  us-gaap:Assets                    FY2026  125,254,000,000 / 111,601,000,000
```

## The answers are refusals, not wrong answers

Recovered from checkpoints. Verbatim openings:

> **g-001** — *"What was Tesla's total revenue in fiscal year 2025?"*
> "The evidence provided does not contain Tesla's total revenue for the full
> fiscal year 2025. The most recent figure available is for the nine months
> ended September 30, 2025, which shows total revenues of $69,926 million…
> Without Q4 2025 data from the evidence, the full-year total cannot be
> determined."

> **g-002** — *"What was Apple's net income in fiscal year 2025?"*
> "The provided evidence excerpts do not include Apple's full fiscal year
> 2025 net income figure directly."

> **g-016** — R&D as a share of revenue, three companies.
> "The evidence contains **no R&D expense figure** and **no full-year revenue
> figure** for Tesla… **Tesla's R&D-to-revenue ratio cannot be calculated
> from the provided evidence.**"

The system is not fabricating and not contradicting the filings. It is
declining. That is why these misses are invisible to every gate metric.

## The data is present and retrievable — traced end to end for g-001

Chain of custody for the single figure `$94,827M` (Tesla FY2025 total
revenue), each step verified:

1. **In the facts store**, twice, from the FY2025 10-K:
   `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax` and
   `us-gaap:Revenues`, label `Revenues`, FY2025, accession
   `0001628280-26-003952`.
2. **That filing is fully ingested:** manifest row
   `('0001628280-26-003952', '10-K', '2026-01-29', 'embedded', 236 chunks,
   1365 facts)`.
3. **No chunks were lost at embed:** `SUM(n_chunks)` over embedded filings =
   **4181**, and the Qdrant collection
   `fie_chunks__text-embedding-3-small` holds exactly **4181** points.
4. **The chunks are in Qdrant** under `doc_id =
   TSLA-10-K-0001628280-26-003952`, including **50 chunks in Item 8**
   (financial statements).
5. **A chunk contains the literal string:** `94,827` appears in
   `data/chunks/0001628280-26-003952.json` (the 10-K) and
   `data/chunks/0001628280-26-003837.json` (the Q4 earnings 8-K).

So this is not an ingest gap, not an embedding gap, and not a label bug.

## The retriever does not surface it

Running the production retriever (`build_production_retriever`) at the
production `k=8`:

```
QUERY: "What was Tesla's total revenue in fiscal year 2025?"
  rank of chunk containing '94,827': NOT RETRIEVED
  top hits: 10-Q Part I Item 1 notes (x3), then 8-K EX-99 press releases (x3)

QUERY: "Tesla total revenues fiscal 2025 consolidated statements of operations"
  rank of chunk containing '94,827': 3   (0001628280-26-003952:item-8-p007)
```

The analyst's natural phrasing retrieves quarterly filings; the 10-K's Item 8
statement chunk only surfaces when the query is rewritten toward
financial-statement vocabulary.

## Scope note on ADR 0011 (evidence, not a recommendation)

ADR 0011 settled retrieval, and the plan carries a standing guard against
re-tuning it. What that ADR actually measured, for the record:

- **50 queries** in `eval/retrieval_queries.jsonl`, labels are
  **`SectionRef`** — ticker + form + *section*, so **any** chunk in the right
  section counts as a hit.
- Winning arm (OpenAI, no rerank): **recall@5 0.620, recall@10 0.740,
  recall@20 0.860, MRR 0.579, nDCG@10 0.528.**

Two properties of that setup are relevant to the evidence above: the labels
are section-level rather than "the chunk containing the figure", and the
measured recall@5 of 0.620 is against those looser labels. Production runs
at `k=8`. Whether ADR 0011's "settled" status was ever intended to cover
end-to-end figure delivery is a scope question this document only raises,
does not answer.

## Risks

1. **The gate suite measures precision only.** `grounding_rate`,
   `hallucination_rate` and `numeric_fidelity` are all conditioned on the
   claims the system chose to emit. Declining to answer scores *perfectly*
   on all three — fewer claims, each verifiable. `expected_fact_coverage` is
   the only recall-style signal in the suite, and it is not a gate. A pass
   is therefore compatible with a system that answers very little.
2. **The accepted baseline now encodes 0.393.** Future runs are compared
   against a baseline that treats this coverage as acceptable, and no gated
   metric would flag it degrading further.
3. **Product risk for Phase 4.** The failing question set includes the most
   basic lookups in the corpus ("total revenue", "net income", "total
   assets", "R&D expense"). A deployed backend that answers "not in
   evidence" to those is trustworthy but not useful.
4. **Same structural pattern the project already named.** The plan's
   "governing lesson" is that a metric can read well by routing *around* the
   failure it should catch (the retraction bug; the `numeric_fidelity`
   noise). The difference here is that the metric is not broken — it is
   accurate and simply not gating.
5. **Direction of the corpus gap is unknown.** This evidence establishes the
   failure for g-001 specifically and the shape for the other 16. It does
   **not** establish whether the cause is uniform across them.

---

# Issue 2 — `could not locate claim … for excision` and claim staleness

## Where the log line comes from

`src/fie/graph/build.py`, `retract` node. It calls `excise_claim`
(`src/fie/graph/retract.py`), which removes the *sentence* carrying a claim,
matching either an exact substring or **≥ 50% token overlap**
(`MIN_OVERLAP = 0.5`). When neither matches it logs:

```
could not locate claim <id> in draft for excision;
text remains in the answer and still counts as unsupported
```

and **deliberately leaves the claim `unsupported` rather than `retracted`**.
That behaviour is the 2026-07-26 fix: marking it retracted would route it out
of the grounding rows and hide surviving unsupported text from
`hallucination_rate`.

## Frequency and the claims involved

- **15 occurrences** in the accepted run's log.
- **4 claims** ended the run with status `unsupported` — exactly the
  `hallucination_rate` numerator (4/275 = 0.01454, matching the reported
  value).

For each of those 4, the highest token overlap against any sentence in its
own final answer body (retracted section excluded):

| claim | overlap | reading |
|---|---|---|
| g-011 `claim-4-22` | 0.38 | text absent — no sentence makes this claim |
| g-011 `claim-4-29` | 0.40 | text absent |
| g-013 `claim-4-5` | 0.25 | text absent (nearest sentence is a different figure) |
| g-013 `claim-4-6` | 0.33 | core assertion present, derivation operands absent |

Example — g-011 `claim-4-22` reads *"Data Center networking grew 79%
year-on-year … driven by NVLink compute fabric for GB200 and GB300
systems…"*. The closest sentence in the delivered answer is about GPU
architecture launches (Hopper/Blackwell) and makes no networking-growth
claim. The text is not in the answer.

## Mechanism: claims outlive the draft they describe

Both affected questions ran `verify_iteration=3, critic_iteration=2` — the
critic looped back and synthesis rewrote the draft. The `claims` channel
still carries claims describing the *earlier* draft, while `draft_answer`
holds the rewritten one. Excision then fails because the sentence is already
gone.

Measured across the whole accepted run — share of claims whose best token
overlap with their own final answer body is below the 0.5 threshold:

```
31 / 312 claims = 9.9%

worst questions (orphans / total claims):
  g-011  10/31      g-012   3/16      g-002   1/9
  g-013   7/18      g-017   3/20      g-009   1/17
  g-004   3/8       g-010   2/13
```

## Incidental finding inside this population

g-013 `claim-4-6` — *"Products revenue declined approximately 6% in FY2023,
calculated from $298,085 million in FY2023 and $316,199 million in FY2022"* —
is **arithmetically correct** ((316,199 − 298,085)/316,199 = 5.73%), and both
operands are in the store as label `Products` (FY2023 and FY2022
respectively). It was ruled `unsupported` with feedback *"searched XBRL
concepts: ProductsRevenue"*.

Confirmed cause: the store's term matching (including the ADR 0018
space-stripped label comparison) uses `LIKE '%<term>%'`, so the mapper's
concatenated term `ProductsRevenue` cannot match the label `Products` —
substring direction is wrong. Measured directly against `data/ingest.db`:

```
AAPL rows matching '%ProductsRevenue%' (concept, label, stripped label):   0
AAPL rows matching '%Products%' (label):                                  78
```

ADR 0018's fix bridges `NetSales` → "Net sales" (a *space* difference); it
does not bridge a suffix the filed label does not carry. This claim is a
vocabulary false negative counted as a surviving hallucination.

## Risks

1. **The warning is not evidence of surviving hallucinated text.** For 3 of
   the 4 cases the text is genuinely absent. Reading the log line at face
   value overstates the problem, and could drive work at the wrong target.
2. **`hallucination_rate` is pessimistic, not optimistic.** Of the 4 counted
   survivors, ~1 has text in the delivered prose (and that one appears to be
   a correct claim failed on vocabulary). The gate result is therefore
   conservative — the error runs in the safe direction, but the number does
   not literally mean "4 hallucinations reached the reader".
3. **~10% of the scored population is stale.** `grounding_rate`,
   `hallucination_rate` and `citation_accuracy` are computed over claims of
   which roughly one in ten does not correspond to the answer actually
   delivered. The pass has margin (0.9345 vs 0.90) and orphans are mostly
   grounded, so the ratio is not obviously inflated — but the population and
   the artifact are not the same object, and no current check detects drift
   between them.
4. **User-visible false warning.** For stranded claims the answer appends:
   *"They could not be located for removal, so they may still appear above —
   treat them as unverified."* For orphans this warns a reader about text
   that is not present, in the answer the reader is being handed.
5. **The staleness is unbounded in principle.** It scales with critic and
   verify loop-backs; the two worst questions here are the two with the most
   re-synthesis. Nothing currently caps or reports it.

---

# Appendix — reproduction

All of this is re-derivable offline from committed artifacts, except the
retrieval probe (one embedding call, needs `make qdrant-up` and keys).

**Recover answers, claims and verdicts from checkpoints.** Run ids are in the
report; `thread_id` is the run id; the newest checkpoint holding both
`claims` and `verdicts` is the final state.

```python
import json, sqlite3, warnings
warnings.filterwarnings("ignore")  # unregistered-type msgpack noise
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

report = json.load(open("eval/reports/agent-20260728-212914.json"))
serde, conn = JsonPlusSerializer(), sqlite3.connect("data/checkpoints.db")

for qid, run_id in report["run_ids"].items():
    for typ, blob in conn.execute(
        "select type, checkpoint from checkpoints where thread_id = ?"
        " order by checkpoint_id desc", (run_id,)
    ):
        vals = serde.loads_typed((typ, blob)).get("channel_values", {})
        claims, verdicts = vals.get("claims"), vals.get("verdicts")
        if claims and verdicts:
            draft = vals.get("draft_answer") or ""
            # vals also has: evidence, verify_iteration, critic_iteration, error
            break
```

**Orphan measurement.** Split `draft_answer` on the literal
`"The following claims"` to drop the retracted section, then score each
claim against the body with the same tokenizer excision uses:

```python
from fie.graph.retract import _tokens, _split_sentences
body = draft.split("The following claims")[0]
sents = [s for p in body.split("\n\n") for s in _split_sentences(p)]
ct = _tokens(claim.text.strip().rstrip("."))
best = max((len(ct & _tokens(s)) / len(ct) for s in sents), default=0.0)
# best < 0.5 (MIN_OVERLAP) == excision would fail == orphan
```

**expected_fact_coverage breakdown.** Re-run the body of
`expected_fact_coverage` (`src/fie/eval/agent_runner.py`) over
`eval/gold_set.jsonl` and the recovered answers, splitting misses into
"unresolved in store" vs "resolved but figure absent from answer".

**Chunk/embedding integrity.**

```bash
sqlite3 data/ingest.db "select sum(n_chunks) from filings where status='embedded'"   # 4181
curl -s localhost:6333/collections/fie_chunks__text-embedding-3-small \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['points_count'])"  # 4181
grep -l "94,827" data/chunks/*.json
```

Note when filtering Qdrant: `accession_no` is **not** a top-level payload
key (it is nested inside the stringified `metadata` value). Filter on
`doc_id` or `chunk_id` instead — a filter on `accession_no` silently returns
zero points.

**Retrieval probe.**

```python
from fie.config import Settings
from fie.runner import build_production_retriever
s = Settings(); r = build_production_retriever(s)
hits = r.retrieve("What was Tesla's total revenue in fiscal year 2025?", s.retrieval_k)
print([i for i, h in enumerate(hits) if "94,827" in h.text])   # []
```
