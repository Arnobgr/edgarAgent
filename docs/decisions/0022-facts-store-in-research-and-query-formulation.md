# ADR 0022 — The facts store answers research; queries reach retrieval in filing vocabulary

**Date:** 2026-07-31 · **Phase:** 3 (post-gate remediation) · **Status:** accepted ·
**Scope guard:** this does **not** re-open ADR 0011

## Context

`expected_fact_coverage` decomposed into three causes over 17 misses: **13
retrieval, 2 context packing, 2 synthesis, 0 corpus gap**. Two facts about
the 13 set the shape of the fix.

**Every one of the 28 expected figures resolves in the XBRL facts store.**
`query_facts` returns rows for 28 of 28. The store is wired into `verify`
(the reconciler) and **not** into `research`. `plans/00-overview.md` §3.3
decided that numeric truth lives in XBRL rather than in parsed HTML cells;
only the verification half was ever implemented. **The system could check a
number it could not look up** — and the 13 misses are standard flow facts
(revenue, net income, R&D) sitting in a local SQLite table the answering path
could not reach.

**Query formulation is the dominant retrieval lever, and no such stage
existed.** `research` retrieves with `query = state.query or
sub_question.text` — the loop-back override or the decomposer's phrasing,
neither of which is filing vocabulary. The 2026-07-30 ablation over the 13:

| lever | effect |
|---|---|
| raise `k` alone | 3 of 11 at k=20, 5 at k=30, 6 at k=50 — least value per token |
| remove the query-implied filter | **worse** (10→47, 12→19, 20→26); `infer_filter` is not the problem |
| dense only, no BM25 | better in 7 of 11 — sparse dilutes this query class |
| **statement-vocabulary rewrite** | **10 of 11 inside the top 10** (ranks 2,3,4,4,7,7,7,8,10,10) |

## Decision

### C1 — a deterministic fact lookup on the research path

`research` maps its query to filed figures and injects them as evidence
chunks carrying their own accession, so a claim citing one keeps the same
citation trail as a claim citing filing prose. A lookup, a formatter and a
wiring change — explicitly not a subsystem.

Three details are load-bearing rather than incidental:

- **Injected chunks outrank retrieved ones in the fan-in packing sort.** Two
  of the 17 misses were figures retrieved into `evidence` and then cut by
  `chunks_per_sub_question` before the model saw them. An exact filed figure
  must not be droppable that way.
- **`chunk_type` is `xbrl`, not `table`.** The reconciler's table fallback
  cell-matches chunk text; letting it match this would turn the store into
  its own corroboration.
- **A year-scoped balance resolves to the year's closing date**, the same
  rule ADR 0021 gives the reconciler.

Injection is **additive**: an empty mapping — which is what the prompt
demands for any question prose answers — or a model failure leaves the branch
with exactly the evidence it had before. Non-dimensioned facts only: a
segment breakdown must never stand in for a company total.

The lookup gets its **own store connection**. The graph reads it from
research branches while `verify` reads the reconciler's; one connection
across both would widen the close/query race ADR 0019 closed.

### C2 — one query rewrite, inside `research`

**One rewrite, not a multi-query fan-out.** Our own ablation says one is
enough, so building variants is speculative work against a measurement that
passed. Cost multiplies badly here — the call is per-branch inside a parallel
`Send` fan-out, and branches multiply again on the verify→research loop-back.

**No new graph node.** A separate `formulate` node would need its own channel
and would change the state machine, for a per-branch step with no fan-in. The
rewrite sits inside `research`, before `retriever.retrieve`; the prompt lives
in `context/`; the role resolves through `get_model("query_rewrite")` so no
model id appears at a call site. No guardrail work — `research` is already
inside `guarded()` and `BudgetTracker` picks the cost up as a callback.

**A failed rewrite costs ranking, not the branch.** It falls back to the
analyst's phrasing.

**The query actually searched is recorded** on `branch_results`, which
already carries a reducer, so no new channel is needed — and without it a
retrieval failure is indistinguishable from a rewrite failure, which the next
diagnosis session would pay for.

**`{raw, rewritten}` union is measured and rejected.** It was carried as a
hedge against a rewrite that scores worse than the original phrasing. The
curve does not earn it — it is worse than the rewrite alone at every depth,
because interleaving the raw list back in displaces good results with the
ones the rewrite was written to beat. Measured over ADR 0020's 175 figure
labels, rewrites memoized so the arms compare the *same* rewrite rather than
two samples of a non-deterministic one:

| arm | r@5 | **r@8** | r@10 | r@20 | r@50 |
|---|---|---|---|---|---|
| raw (previous production) | 0.274 | **0.354** | 0.400 | 0.526 | 0.617 |
| rewritten | 0.337 | **0.451** | 0.503 | 0.617 | 0.674 |
| {raw, rewritten} union | 0.320 | **0.423** | 0.474 | 0.611 | 0.674 |

`r@8` is the number that matters — `retrieval_k` is 8. The rewrite moves
figure delivery **0.354 → 0.451**, a 27% relative gain, for one cheap call
per branch. The union is adopted nowhere and stays available as an
`eval-retrieval` arm.

### C3 — `retrieval_k` and the fan-in cap stay where they are

The same curve says a bigger `k` would buy more: 0.451 → 0.617 at k=20. It is
still refused, and the reason is now measured rather than asserted.

Raising `retrieval_k` alone changes nothing, because
`chunks_per_sub_question` (8) cuts the extra chunks back out before the model
sees them — the packing keeps the highest-scored 8 per sub-question, which
are approximately ranks 1-8 either way. Converting r@20 into delivered
figures means raising **both**, which is ~2.5x the evidence text in the
synthesis window, on the most expensive role in the system.

And it would buy figures C1 already delivers exactly, at no context cost:
ADR 0020's 175 labels *are* headline annual figures, which is precisely the
class the facts lookup resolves deterministically. Paying 2.5x synthesis
context for figures already in the window is the worst trade in this plan.

Both values stay at 8. The curve is recorded here so a future session can
revisit it with a measured cost-per-run delta, if the re-baseline shows
figures still missing that a deeper `k` would have caught.

## Consequences

- Both halves of the corpus are served: C1 answers "what is this figure"
  exactly, C2 serves the narrative half C1 cannot.
- Two extra cheap-model calls per research branch (lookup + rewrite), each
  multiplied by the fan-out and again by any loop-back. Both are
  `google/gemini-2.5-flash`; the fan-out is bounded at 5 and verify passes at
  2, so the ceiling is ~20 cheap calls per run.
- `fact_lookup` is its own role rather than reusing `fact_mapping`, so a
  Langfuse trace separates what research looked up from what verify checked.
- The system can now state a figure that no retrieved chunk contains. That is
  the point — the figure is filed, and the reconciler verifies it against the
  same store — but it does mean an answer's citations may point at XBRL rows
  rather than prose, which is a visible change in what a reader sees.

## Scope

ADR 0011 measured 50 queries against `SectionRef` labels and never covered
end-to-end figure delivery; its mandate does not reach this work. Adding a
facts-store tool and a query-formulation stage are **new capabilities, not
parameter tuning**. `retrieval_k` and the fan-in cap are the knobs the
standing guard is about, and they move only on ADR 0020's figure-level
measurements — never to move a gate number. `infer_filter` is left alone:
removing it makes retrieval measurably worse.
