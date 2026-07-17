# Phase 1 — The Cyclic Core (Verifier + Loop-back)

**Goal (blueprint §13):** add the verifier and the verify→research loop-back with bounded iterations. This is the heart of the system — the conditional edge + cycle that makes it a state machine rather than a pipeline (blueprint §4, "the cyclic core"). Get it right before adding breadth.

## Context (read this if starting cold)

Phase 0 delivered a working skeleton: a LangGraph with `research → synthesize` over one Apple 10-K, typed state in `src/fie/state/`, role-based model routing in `src/fie/llm/`, naive BM25 retrieval in `src/fie/tools/`, and full Langfuse tracing. This phase makes verification real: the synthesizer's draft is decomposed into discrete factual claims, each claim is checked against its cited source, and unsupported claims route **back** to research with feedback — or get retracted. The defining property of the whole system (blueprint §1) starts existing here.

Corpus is still one 10-K with naive retrieval; exact XBRL numeric reconciliation arrives in Phase 3 — this phase builds the verifier's *structure* with an interim numeric check, designed so Phase 3 swaps the tool, not the graph.

## Prerequisites

- Phase 0 exit gate passed (run `fie ask` and check the trace before starting).

## Design

### State extensions (`src/fie/state/`)

- `Claim`: `id`, `text`, `claim_type: Literal["numeric","qualitative","forward_looking"]`, `source_chunk_ids: list[str]`, `status: Literal["pending","grounded","unsupported","partial","retracted"]`.
- `Verdict`: `claim_id`, `verdict: Literal["grounded","unsupported","partial"]`, `supporting_span: str | None` (the exact quoted source text), `method: Literal["numeric_reconciliation","semantic","attribution"]`, `feedback: str | None` (why it failed — this text drives re-retrieval).
- `EngineState` gains: `claims`, `verdicts`, `verify_iteration: int`, `rejection_feedback: list[str]`, and real `run_budget` fields (`tokens_used`, `cost_usd`, plus configured caps).

### New/changed nodes (`src/fie/graph/`)

1. **`synthesize` (changed):** now emits structured output — the draft answer **plus** the claim list, each claim tagged with its cited chunk ids. Use the model's structured-output support; a claim without a citation is auto-`unsupported`.
2. **`verify` (new — the meat):** fans out per claim *within the node* via `asyncio.gather` (cross-node fan-out with `Send` is Phase 2's job; per-claim concurrency doesn't need graph topology). Routes internally by `claim_type` (blueprint §4):
   - **numeric** → `tools/reconcile.py`: interim implementation — parse numbers (with scale words: million/billion, %, $) from claim and from cited chunk text; verdict `grounded` only on exact match after normalization. Written against a `NumericReconciler` protocol so Phase 3 replaces it with the XBRL facts lookup without touching the node.
   - **qualitative** → cheap-model entailment judge (`verify-qualitative` role): claim vs. cited span → verdict + supporting span, structured output.
   - **forward_looking** → cheap-model attribution check (`verify-forward` role): the source must actually contain the forward-looking statement, attributed as such.
3. **`loop_back` decision (new — conditional edge):** after `verify`:
   - all claims `grounded`/`partial`-accepted → proceed to `critic`.
   - any `unsupported` **and** `verify_iteration < MAX_VERIFY_ITERATIONS` (config, default 2) → route back to `research` with `rejection_feedback` ("claim X: figure not found in cited section — re-retrieve or retract"); research re-runs *only* for failed claims' topics; synthesize revises the draft.
   - iterations exhausted → remaining `unsupported` claims are **retracted**: removed from the answer body and listed in a "retracted claims" section of the output. Never silently dropped (blueprint §4).
4. **`critic` (new — quality gate):** rubric check via the `critic` role (`claude-opus-4-8`): question actually answered, all surviving claims grounded, no uncited factual statements. Fails → back to `synthesize` once (bounded); passes → END. (The full "back to decomposer" edge lands in Phase 2 when a decomposer exists; the human-in-the-loop gate lands in Phase 4 with the API.)

### Guardrails (`src/fie/guardrails/`) — first real implementation

- **Iteration caps** on both cycles (verify→research, critic→synthesize) enforced in the conditional edges — cycles must terminate (blueprint §4 instruction).
- **Budget tracking:** accumulate tokens/cost per LLM call into `run_budget` (from callback usage data); exceed cap → abort the run cleanly with a partial-result error state, visible in the trace.
- **Node timeouts:** an async timeout wrapper applied to every node.
- **Retry-with-backoff** on LLM/tool transient failures (`tenacity` or hand-rolled, one place, not per-node).

### Persistence

Wire LangGraph's `SqliteSaver` checkpointer (decision 3.4 in `00-overview.md`): every node transition checkpoints under a `thread_id` = run id; a killed run resumes with `graph.invoke(None, config)`. Checkpoint DB lives in `data/`.

### Output

The run result is now `answer + grounding table`: per claim — text, type, verdict, supporting span, source metadata (document, section, accession number), iterations consumed. This JSON structure is the contract the Phase 4 API and Phase 5 frontend render; define it as a Pydantic model in `state/` now.

## Tests

- Unit: numeric normalization ("$391.0 billion" ≡ "391,035" in millions — build a small table of tricky cases), claim-type routing, iteration cap enforcement, budget abort.
- **The planted-lie test (the phase's signature test):** feed the synthesizer a mocked draft containing a fabricated figure and a fabricated qualitative claim → assert the verifier flags both, the loop-back fires with feedback, and after exhaustion both are retracted and listed. Run with mocked LLMs for CI determinism, plus one live variant run manually.
- Crash-resume: kill after `verify` checkpoint, resume, assert completion without re-running earlier nodes.

## Exit gate

- Planted-lie test green; a real `fie ask` run shows a full grounding table.
- A Langfuse trace of a run with ≥1 loop-back iteration exists and clearly shows: verify fan-out per claim, the conditional edge decision, the feedback carried back, the revised draft (blueprint: every loop iteration visible in the trace).
- Budgets, timeouts, iteration caps demonstrably enforced (each has a test).
- ADRs: verdict taxonomy, interim numeric reconciliation + the Phase 3 swap plan, checkpointing.
