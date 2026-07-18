# ADR 0006 — Checkpointing with SqliteSaver

**Date:** 2026-07-18 · **Phase:** 1 · **Status:** accepted

## Decision

Every graph run checkpoints after every node transition via LangGraph's
`AsyncSqliteSaver` into `data/checkpoints.db` (gitignored), keyed by
`thread_id` = the run id. A killed run is resumed with
`graph.ainvoke(None, config)` under the same thread id — exposed as
`fie resume <run-id>` in the CLI; completed nodes are not re-executed
(covered by `tests/test_resume.py`).

The async saver (not the sync `SqliteSaver`) is used because the Phase 1
nodes are async — the per-claim verify fan-out and the timeout guardrail
both need `await` — and the sync saver does not implement the async
checkpoint interface.

## Rationale

Decision 3.4 in `plans/00-overview.md`: a full run is minutes of paid LLM
calls; a crash without checkpoints re-buys all of it. SQLite is the
lightest durable option for a single-process engine, and checkpoints double
as a replay/debugging aid alongside the Langfuse trace. Phase 4's async job
model leans on this same mechanism for crash recovery instead of a broker.

## Revisit

Postgres checkpointer only if/when multi-process serving demands it — the
explicit upgrade trigger from overview §3.1/3.4; it won't in v1.
