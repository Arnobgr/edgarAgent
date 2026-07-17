# Phase 7 — Cost/Routing Note + Engineering Rigor Pass

**Goal (blueprint §10/§13):** the model-routing decision note with real cost math, a test-coverage pass, and the README architecture document. This phase produces the artifacts that close the "cloud & licensing fluency" and "software engineer, not scripter" claims (blueprint §7/§14). Nothing new is built; what exists gets measured, documented, and tightened.

## Context (read this if starting cold)

The system is complete and deployed (Phases 0–6): LangGraph cyclic multi-agent engine (decompose → parallel research → synthesize → per-claim verify with XBRL-exact numeric reconciliation → bounded loop-back → critic → HITL gate), Qdrant hybrid RAG over TSLA/AAPL/NVDA EDGAR filings, Langfuse self-hosted on the VPS, FastAPI + Docker + GitHub Actions CI running a regression eval suite (`eval/baselines.json`), React grounding-table frontend on Cloudflare Pages, security hardening + adversarial evals. Model routing is role-based config (`src/fie/llm/`): `claude-opus-4-8` for synthesis/critic, a cheap OpenRouter model for mechanical roles, deterministic XBRL lookup for numeric verdicts.

## Deliverable 1 — Cost & routing decision note (`docs/decisions/model-routing-and-cost.md`)

Real math from real data — Langfuse has per-node token/cost for every run since Phase 0:

1. **Measure:** from the full gold-set eval run, extract per-role token usage (input/output split) and derive **cost per analyst question** — mean and p90 — broken down by node (decompose / research×n / synthesize / verify×claims / critic), plus loop-iteration overhead.
2. **Model the alternatives** (same token counts, different unit prices — state pricing as of the note's date):
   - Synthesis/critic on `claude-opus-4-8` ($5/$25 per M tokens) vs `claude-sonnet-5` ($3/$15) vs everything-on-frontier vs everything-on-cheap.
   - **Run, don't just model, the one live experiment that matters:** the full eval set with synthesis on `claude-sonnet-5`. If eval metrics hold, the cheaper routing wins and *becomes* the default — that's the blueprint's own logic (complexity and cost both earned against the output). Record the metric deltas either way.
3. **Verifier routing confirmation:** restate the Phase 3 measured verifier P/R per claim type and the escalation decision it produced; include the cost of the escalated path.
4. **Close with the fluency paragraph** the blueprint asks for: cost per question at chosen routing, how it scales with sub-questions/claims per question, and the build-vs-buy framing (OpenRouter for price-shopping mechanical calls, Anthropic direct for high-stakes; Langfuse self-hosted vs LangSmith SaaS — the licensing/data-control tradeoff, stated interview-ready per blueprint §6).

## Deliverable 2 — Rigor pass

- **Coverage:** measure with `pytest --cov`; target ≥80% on `src/fie/` with the gaps triaged (graph edge decisions, guardrails, reconciliation, and state serialization must be at ~100% — they're the load-bearing logic; thin API glue may sit lower, justified). Add tests where the triage demands, don't chase the number.
- **Type/lint debt:** zero `mypy` ignores without a comment; `ruff` clean including docstring rules on public modules.
- **Error-handling sweep:** every external call site (EDGAR, Qdrant, LLM providers, Langfuse) has a defined failure behavior and a test for it — no bare excepts, no failure path that leaves a job stuck outside the Phase 4 recovery states.
- **Logging sweep:** one structured event per node transition and per tool call with `run_id` correlation; no print debugging left; log levels sane in prod.
- **Dead-code / TODO sweep:** every remaining TODO gets an issue or gets done; unused Phase 0 placeholders removed. Apply the honest test one last time: any component that never earned a measurable improvement gets cut and the cut recorded (that note is itself portfolio material).

## Deliverable 3 — README architecture doc

The repo's front page for a technical reviewer (many will read *only* this):

1. What it is + the defining property (verification is the point) in 5 lines; the grounding-table screenshot/GIF (Phase 5) up top.
2. Architecture: the harness-layer table (blueprint §3) mapped to actual modules; the graph diagram (mermaid, matching the real topology including the cycle and HITL gate).
3. **The numbers:** current eval results — grounding rate, hallucination rate, citation accuracy, numeric fidelity, verifier P/R — with a one-line description of how each is measured, and cost per question. Metrics a reviewer can't get from a demo.
4. Retrieval section (hybrid + reranking justification with the measured comparison — blueprint §5 says a retrieval reviewer reads this closely), RAG/ingestion overview, XBRL-as-ground-truth insight.
5. Decision index: link every ADR in `docs/decisions/`; scope boundary section (what's deliberately not built and the triggers — lift from blueprint §11); link to `docs/security.md`.
6. Honest claims paragraph aligned with blueprint §14 — claim the single-process cyclic orchestration precisely; explicitly not distributed-at-scale.
7. Run-it-yourself: local quickstart (compose up, ingest, ask) verified on a clean checkout.

## Exit gate

- Cost note complete with measured (not estimated) per-question costs and the sonnet-vs-opus synthesis experiment recorded; routing config matches the note's conclusion.
- Coverage target met per the triage; error-handling and logging sweeps done with diffs to show.
- README complete; a cold clean-checkout quickstart works as written.
- Full eval suite green — unchanged or improved vs the Phase 6 baseline.
