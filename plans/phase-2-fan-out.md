# Phase 2 — Fan-out (Decomposer + Parallel Research + Fan-in)

**Goal (blueprint §13):** add question decomposition, parallel research sub-agents, and fan-in synthesis. After this phase the topology matches the blueprint §4 diagram in full (minus the human gate, which lands with the API in Phase 4): genuinely multi-agent, with fan-out, fan-in, conditional routing, and the Phase 1 cycle.

## Context (read this if starting cold)

Phases 0–1 delivered: a LangGraph over one Apple 10-K with `research → synthesize → verify → (loop-back | critic)`, typed state, per-claim verdicts, retraction, guardrails (iteration caps, budgets, timeouts), SQLite checkpointing, and full Langfuse tracing. The graph is still single-track: one research pass per question. Real analyst questions ("how has the margin profile and capital allocation shifted over three years, and what are the risks?") are multi-part; this phase splits them and researches the parts in parallel.

To make parallelism *real* (not three lookups in the same document), this phase also widens the naive corpus: fetch the latest 10-K **and the last ~4 10-Qs** for Apple via the existing `edgartools` path, still with BM25 retrieval. Production ingestion of all three companies is Phase 3.

## Prerequisites

- Phase 1 exit gate passed (planted-lie test green, loop-back visible in a trace).

## Design

### State extensions (`src/fie/state/`)

- `SubQuestion`: `id`, `text`, `rationale`, `status: Literal["open","researched","exhausted"]`.
- `EngineState`: `sub_questions: list[SubQuestion]`; `evidence` becomes an accumulating channel (LangGraph reducer, e.g. `Annotated[list[EvidenceChunk], operator.add]`) so parallel branches merge without clobbering. Evidence chunks gain a `sub_question_id`.

### New/changed nodes (`src/fie/graph/`)

1. **`decompose` (new, entry node):** the `decompose` role (cheap model, structured output) splits the question into independent sub-questions (cap: config, default 5), each with a rationale. **Trivial-question passthrough (blueprint §4 — no ceremony):** if the question is single-part, emit exactly one sub-question and route straight to research; the decision must be visible in the trace.
2. **Research fan-out:** use LangGraph's `Send` API — the conditional edge after `decompose` emits one `Send("research", {...})` per open sub-question; instances run in parallel, each retrieving for its own sub-question and writing into the accumulating evidence channel. Per-branch failures don't kill the run: a failed branch marks its sub-question `exhausted` and the synthesizer notes the gap.
3. **`synthesize` (fan-in, changed):** now merges evidence across sub-questions into one coherent draft; claims additionally carry `sub_question_id` so verification failures can be traced to a branch.
4. **Loop-back (changed):** the Phase 1 verify→research edge now targets *specific* sub-questions — feedback for a failed claim re-runs research (again via `Send`) only for that claim's sub-question, with the rejection feedback in the branch's input. Iteration caps unchanged.
5. **`critic` (changed):** the "rubric fails → back to decomposer" edge from the blueprint diagram now exists (bounded: 1 re-decomposition per run) for the case where the answer fails because the question was mis-split, not mis-researched.

### Guardrails

- Cap concurrent research branches (semaphore, config default 5) — fan-out multiplies token spend; the Phase 1 budget accounting must aggregate across parallel branches correctly (verify this explicitly — concurrent updates to the budget counters are the easy bug here; use a reducer channel for usage, not in-place mutation).

### Context management (`src/fie/context/`)

Fan-in is where context pressure first appears: n branches × k chunks can exceed what synthesis should see. Implement evidence packing: per-sub-question chunk budget, dedupe identical chunks retrieved by multiple branches, order by relevance. Keep it simple; measure in Phase 3.

## Tests

- Unit: decomposer passthrough (trivial question → 1 sub-question, no extra LLM ceremony), `Send` fan-out produces one branch per sub-question, evidence reducer merges without loss/duplication, branch-failure isolation, budget aggregation under concurrency.
- Integration (mocked LLMs): a 3-part question runs 3 parallel branches, fan-in synthesis cites evidence from all 3, targeted loop-back re-runs exactly 1 branch.
- Live smoke: the blueprint's own example question ("margin profile and capital allocation over three years, and the risks") against Apple's 10-K + 10-Qs.

## Exit gate

- The live smoke question produces an answer with a grounding table drawing on multiple filings, and its Langfuse trace shows: the decomposition decision, parallel research spans (overlapping in time), fan-in, and — if triggered — a targeted loop-back to a single branch.
- A trivial question demonstrably skips decomposition ceremony in the trace.
- All Phase 1 tests still green (the cycle must survive the topology change).
- ADRs: `Send`-based fan-out, evidence reducer + budget aggregation, branch caps.
