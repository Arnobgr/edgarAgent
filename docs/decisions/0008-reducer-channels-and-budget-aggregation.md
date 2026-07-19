# ADR 0008 — Reducer channels for fan-in and budget aggregation

**Date:** 2026-07-19 · **Phase:** 2 · **Status:** accepted

## Decision

Every state channel that parallel research branches write is a LangGraph
reducer channel; everything else stays last-write-wins and is only ever
written by sequential nodes:

| Channel | Reducer | Written by |
| --- | --- | --- |
| `evidence` | `merge_evidence` — accumulate, dedupe per (sub_question_id, chunk_id) | research branches |
| `branch_results` | `operator.add` | research branches |
| `run_budget` | `merge_budgets` — most-used snapshot wins | every node (guardrail stamp) |
| `error` | `keep_first_error` | guardrail wrapper |
| `sub_questions` | none (overwrite) | decompose, synthesize only |

Token/cost accounting itself stays in the Phase 1 `BudgetTracker`: a
thread-safe (locked) callback object shared by all branches, outside graph
state. Nodes stamp `tracker.snapshot()` into `run_budget`; the reducer only
resolves concurrent stamps of that monotonic snapshot. Branches report
their outcome on the accumulating `branch_results` channel, and the
synthesizer (fan-in, sequential) stamps sub-question statuses
(`researched`/`exhausted`) from the latest result per sub-question.

## Rationale

Without reducers, two branches finishing in the same superstep raise
`InvalidUpdateError` (or silently clobber each other, depending on the
channel). The guardrail wrapper made this acute: it stamps `run_budget`
into *every* node update, so even a branch that only returned evidence
would collide on the budget channel. Keeping accumulation in the locked
tracker (rather than summing usage deltas in a state reducer) means the
budget check (`tracker.exceeded`) is always against the true global total
mid-superstep — a state-channel sum would only be consistent at superstep
boundaries, letting parallel branches overshoot the cap together.

Sub-question status is the one piece of branch-owned data kept out of the
branches' writes: merging status updates by id needs replace semantics on
re-decomposition, which a reducer cannot express cleanly, so statuses are
derived at fan-in from `branch_results` instead.

## Revisit

- Phase 3: per-branch spend attribution (which branch cost what) wants
  usage recorded per `sub_question_id` — extend `branch_results`, not the
  tracker.
- Evidence dedupe for the *prompt* (identical chunk via two branches) is
  context packing's job (`pack_evidence_by_sub_question`), not the
  reducer's; the reducer keeps both tags so loop-back targeting stays
  accurate.
