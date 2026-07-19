# ADR 0007 — Send-based research fan-out

**Date:** 2026-07-19 · **Phase:** 2 · **Status:** accepted

## Decision

Parallel research runs as a graph-level fan-out using LangGraph's `Send`
API, not `asyncio.gather` inside a node. The conditional edge after
`decompose` emits one `Send("research", ResearchBranch(...))` per open
sub-question; the `research` node is registered with
`input_schema=ResearchBranch` (a small Pydantic payload: the sub-question,
an optional query override, and the verifier's feedback) so each branch
receives only its own work item, never the whole state. The verify
loop-back reuses the same mechanism: one `Send` per failed claim, targeting
only that claim's sub-question with the rejection feedback in the payload.
The critic gains the blueprint's `rubric fails → decompose` edge, bounded
to one re-decomposition per run (`max_decompose_iterations = 2`); each
decomposition generation gets fresh sub-question ids (`sq{gen}-{n}`) and
replaces the previous split wholesale.

Trivial questions pass through without ceremony: a zero- or one-item split
becomes exactly one sub-question carrying the original question verbatim,
and single-sub-question runs skip the per-sub-question prompt sectioning.
The decision is visible in the trace as the decompose span's output.

## Rationale

`verify` already fans out per claim with in-node `gather`, which is fine
for homogeneous, cheap checks. Research branches are different: they are
the unit of loop-back targeting, failure isolation, and (from Phase 3)
per-branch token spend — so each branch must be a first-class node
invocation with its own trace span, its own guardrail wrapper, and its own
partial state update. `Send` gives all of that for free and is the
LangGraph-native map-reduce shape; overlapping research spans in Langfuse
fall out of it (retrieval runs in `asyncio.to_thread`, so branches
genuinely overlap even while retrieval is synchronous BM25).

## Consequences

- Anything a branch writes must go through a reducer channel (ADR 0008);
  branches never write single-value channels except via reducers.
- Loop-back research queries are the failed claim's text, as in Phase 1,
  but only the failed claim's branch re-runs — untouched branches keep
  their evidence.
- A re-decomposition orphans the previous generation's evidence; it stays
  in the accumulating channel (previously cited chunk ids must remain
  resolvable) and is packed under an "other evidence" section.
