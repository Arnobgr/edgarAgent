# Phase 7 — Cost/Routing Note + Engineering Rigor Pass

**Goal (blueprint §10/§13):** the model-routing decision note with real cost math, a test-coverage pass, and the README architecture document. This phase produces the artifacts that close the "cloud & licensing fluency" and "software engineer, not scripter" claims (blueprint §7/§14). Nothing new is built; what exists gets measured, documented, and tightened.

## Context (read this if starting cold)

The system is complete and deployed (Phases 0–6): LangGraph cyclic multi-agent engine (decompose → parallel research → synthesize → per-claim verify with XBRL-exact numeric reconciliation → bounded loop-back → critic → HITL gate), Qdrant hybrid RAG over TSLA/AAPL/NVDA EDGAR filings, Langfuse self-hosted on the VPS, FastAPI + Docker + GitHub Actions CI running a regression eval suite (`eval/baselines.json`), React grounding-table frontend on Cloudflare Pages, security hardening + adversarial evals. Model routing is role-based config (`src/fie/llm/`): `claude-opus-4-8` for synthesis/critic, a cheap OpenRouter model for mechanical roles, deterministic XBRL lookup for numeric verdicts.

## Deliverable 1 — Cost & routing decision note (`docs/decisions/model-routing-and-cost.md`)

Real math from real data — Langfuse has per-node token/cost for every run since Phase 0:

1. **Measure:** from the full gold-set eval run, extract per-role token usage (input/output split) and derive **cost per analyst question** — mean and p90 — broken down by node (decompose / research×n / synthesize / verify×claims / critic), plus loop-iteration overhead.
2. **Model the alternatives** (same token counts, different unit prices — state pricing as of the note's date):
   - Synthesis/critic on `z-ai/glm-5.2` ($0.95/$2.98 per M tokens, the chosen default) vs `claude-opus-4-8` ($5/$25) vs `claude-sonnet-5` ($3/$15) vs everything-on-frontier vs everything-on-cheap.
   - **The routing is decided — GLM 5.2, cost A/B closed (ADR 0045).** The experiments that chose it are run; the planned live `claude-sonnet-5` synthesis experiment is dropped. This note *documents* that outcome — the A/B metric deltas and the cost-per-question math — rather than running a new experiment. The blueprint's own logic applies: complexity and cost both earned against the output, and the cheaper route held the metrics.
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

- Cost note complete with measured (not estimated) per-question costs and the closed cost A/B recorded (GLM 5.2 vs the Anthropic arms, ADR 0045); routing config matches the note's conclusion.
- Coverage target met per the triage; error-handling and logging sweeps done with diffs to show.
- README complete; a cold clean-checkout quickstart works as written.
- Full eval suite green — unchanged or improved vs the Phase 6 baseline.

## Carried in from Phase 6 (2026-08-23)

**ADR 0044 is committed and is not visible anywhere a visitor looks.** The fix
— a change amount that the mapping could not place recomputes from the facts
the claim *cited* — is `b7a5d05` on `dev`, verified three ways: the unit test
fails with the hook removed, a replay of the real run `11f727f75ca6` against
the real corpus turns both claims from `unsupported` to `grounded`, and
`eval-agent --limit 3 --skip-judge` came back 3/3 clean
(`agent-20260823-204731`, `degraded_attempts: 0`). None of that reaches the
deployed surface, for two independent reasons, and they need two different
acts:

- **The landing page is static CDN JSON, not the API.** `recorded/` was
  generated from the 08-13 baseline (ADR 0027), so the two struck-through R&D
  delta claims on run `11f727f75ca6` stay struck through no matter what the
  VPS serves. Only a new accepted baseline → `fie export-runs` → a Pages build
  changes them.
- **The API answers from the image, and the image publishes from `main` only.**
  `dev` publishes nothing. A live `POST /v1/ask` keeps retracting these deltas
  until `main` carries the change and `deploy.sh` has run.

**Ordering, if the new baseline is measured on the box.**
`./deploy/monthly-eval.sh` runs against the *deployed* image by design (ADR
0036), so the deploy has to come first or the run measures the system without
the fix in it. A local `uv run eval-agent` has no such constraint.

**The `LANDING_RUN_ID` trap applies to whatever regenerates `recorded/`.**
Every run id changes, `frontend/src/lib/recorded.ts` hard-codes one, and
Cloudflare Pages answers a missing asset with the SPA shell under **200** — so
the page breaks in the browser with every check green.
`test_the_landing_run_the_spa_hardcodes_is_actually_exported` is the guard and
it lives in pytest because CI runs no frontend step.

**A full run cannot prove this fix worked, and that is not a reason to skip
it.** Two of the twelve retractions across 212 claims in the 30-run baseline
were of this shape, so the expected move in `grounding_rate` is about +0.01 —
an order of magnitude under the 0.078 spread ADR 0043 measured across four
unchanged-system runs. The evidence that the fix works is the unit test and
the replay; the full run's job is the Phase 7 exit gate's own bullet (green,
unchanged or improved), plus producing a baseline clean enough to export
(`degraded_attempts: 0`, ADR 0025).

## Exit gate — met 2026-10-17

Checked bullet by bullet against the repo, not from memory.

- **Cost note** — `docs/decisions/model-routing-and-cost.md`, re-measured on
  the 10-11 baseline from checkpoints and reconciled to the cent against
  Langfuse traces: $0.028 mean, $0.064 p90. The 10-17 baseline reads
  $0.030 / $0.069 from its checkpoints. The cost A/B is closed on GLM 5.2
  (ADR 0045) and `Settings.models` routes synthesis and critic to it.
- **Coverage** — 86% overall (`pytest --cov`, 2026-10-17). Graph 97%,
  guardrails 100%, state 100%, reconciler 95%. The coverage pass is
  `3750b0a`.
- **Error-handling sweep** — `be290e3` pinned the verifier judge, guardrail
  aborts and EDGAR. **Langfuse was the gap, and it was a production one.**
  `get_trace_url`, the only synchronous Langfuse call in a run, raised on an
  unreachable host and failed the question before the graph started. That
  contradicted ADR 0028 and ADR 0032 ("tracing is not on the answer path"),
  and it meant a Langfuse Cloud outage would fail every public question. It
  is now caught, and the run continues untraced with one warning
  (`tests/test_observability.py`, plus a live `fie ask` against a closed
  port). The eval's own judge had the matching instrument fault: a judge
  that judged nothing reported `citation_accuracy: 0.000`. It now reports
  None, and `_incomplete` refuses that run the way it refuses a dropped
  question.
- **Logging sweep** — node transitions were already one structured event
  each, carrying `job_id`/`trace_id` (`8c78a14`, ADR 0028). Two gaps are
  closed: a node that raises now logs `raised` before re-raising, and verify
  logs one event per claim, which is one tool call each (`claim_id`,
  `claim_type`, `verdict`, `verify_method`). Research already logged its two
  tool calls per branch. No `print` is left in `src/`. The one
  `# type: ignore` carries its reason.
- **README and quickstart** — rewritten on 2026-10-13 to the seven points
  above. The quickstart was run on a clean clone: ingest, ask, 3 of 3
  grounded.
- **Full eval green** — the 10-17 baseline (`agent-20261017-131037`) meets
  every floor, and the hallucination ratchet held. *"Unchanged or improved
  vs the Phase 6 baseline" is read through ADR 0043*, which postdates this
  plan. Scores are floored and only the invariant is ratcheted, because one
  reading carries ±0.037 of judge noise alone. Three metrics read below
  08-13 (grounding 0.961 against 0.980, numeric fidelity 0.947 against
  0.989, citation accuracy 0.834 against 0.867), all on a re-ingested corpus
  and inside the spread ADR 0043 measured across unchanged-system runs.
  Against a literal reading of this plan, they are drops.

The Langfuse fix reaches production with the next deploy. Until then, the
server still makes the call that can fail a question.
