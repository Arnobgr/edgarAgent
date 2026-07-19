# ADR 0009 — Research branch caps and failure isolation

**Date:** 2026-07-19 · **Phase:** 2 · **Status:** accepted

## Decision

Fan-out is bounded twice: the decomposer emits at most `max_sub_questions`
sub-questions (default 5, truncated in code, not trusted to the model),
and an `asyncio.Semaphore(max_concurrent_research)` (default 5) bounds
concurrently *running* branches. Retrieval inside a branch runs in
`asyncio.to_thread` so BM25 scoring doesn't block the loop and branches
genuinely overlap.

Branch failures are isolated: a research branch catches its own exceptions,
reports `BranchResult(ok=False)` — its sub-question becomes `exhausted` and
the synthesis prompt says so explicitly — and the run continues on the
surviving branches. Two failure classes stay global by design:
`BudgetExceededError` (over-cap must stop the whole run, not one branch)
and the guardrail node timeout (a hung branch means a sick run, and the
timeout wrapper sits outside the branch's own try/except).

Fan-in context pressure is capped per branch: `chunks_per_sub_question`
(default 8) chunks per sub-question at synthesis, best-scored first,
identical chunks deduped across branches.

## Rationale

Fan-out multiplies token spend and (from Phase 3) retrieval-backend load
linearly in branch count, so both the split width and the concurrency are
config, enforced in code. The semaphore is nearly free while retrieval is
in-memory BM25 but becomes the real guardrail when Phase 3 swaps in
Qdrant + reranking. Marking a failed branch `exhausted` instead of erroring
matches the verdict taxonomy's spirit (ADR 0004): an honest gap in the
answer beats a dead run — the synthesizer is told to state the gap, never
to paper over it.

## Revisit

- The semaphore binds to the event loop of the first run; a compiled graph
  is built per run today (CLI and tests), but if graphs become long-lived
  (Phase 4 API), move the gate to a per-run resource.
- Per-branch retry (currently one attempt; only LLM calls retry) — decide
  in Phase 3 when retrieval becomes a network call.
