# Phase 5 — Frontend (The Grounding-Table Demo Surface)

**Goal (blueprint §8/§13):** a thin but excellent Vite + React UI on Cloudflare Pages whose centerpiece is **the per-claim grounding table** — the answer with each claim color-coded by verdict, hover-to-reveal the exact source span. This single view makes the invisible verification work visible in three seconds; it is the highest-value hiring artifact in the project. Thin scope, high polish.

## Context (read this if starting cold)

Phases 0–4 delivered a deployed FastAPI service (VPS, public reachability via Cloudflare Tunnel, `X-API-Key` auth) with this contract:

- `POST /v1/questions {question, companies?}` → `202 {job_id}`
- `GET /v1/jobs/{id}` → `{status: queued|running|awaiting_review|succeeded|failed, progress: {current_node, verify_iteration}, cost_so_far}`
- `GET /v1/jobs/{id}/result` → `{answer, claims: [{id, text, claim_type, verdict: grounded|partial|unsupported, status (incl. retracted), supporting_span, source: {ticker, form_type, accession_no, filing_date, section_title}, method}], stats: {cost_usd, tokens, iterations, trace_id}}`
- `GET/POST /v1/jobs/{id}/review` for the human-in-the-loop gate.

Exact response models live in `src/fie/state/` (Pydantic) — generate or hand-write matching TypeScript types from them; do not improvise the shape.

## Stack & conventions

- `frontend/` in the monorepo: Vite + React + TypeScript, Tailwind. No heavyweight state library — polling + React Query (or a small fetch hook) is enough. Corpus is fixed (TSLA/AAPL/NVDA), runs are single-user demo flows.
- **When building, load the frontend design skill (`impeccable` / `frontend-design`)** — the blueprint sets an explicit design bar: a serious analyst tool, not a toy. Clean, confident, legible; restrained palette; strong typography.
- API base URL + key via Vite env vars; the demo key is rate-limited server-side (it *will* be public in the bundle — acceptable for a demo; note it, and keep the Phase 4 budget caps as the real backstop).

## Views (three, no more)

1. **Ask.** Question input (with 3–4 example analyst questions as one-click chips, chosen from the gold set so they demo well), company scope selector, submit → navigates to the run view.
2. **Run status.** Poll `GET /v1/jobs/{id}` every ~2s: pipeline progress (decompose → research × n → synthesize → verify → critic) with the current node live, verify-iteration counter, running cost. If `awaiting_review`: render the unresolved claims with accept/retract buttons wired to the review endpoint — the human-in-the-loop gate made tangible.
3. **Result — the killer view (blueprint §8).**
   - The answer rendered as prose with each claim span color-coded: **grounded = green, partial = amber, unsupported/retracted = red**. Claims map to text spans via the claim list (synthesizer already tags claims; ensure the API returns enough to locate each claim's text in the answer — add char offsets to the claim model in Phase 4's contract if missing, coordinate that small backend change here).
   - **Hover (click on touch) a claim → popover:** the exact supporting span quoted, source metadata (company, form type, filing date, section, accession number), verification method (XBRL reconciliation / semantic / attribution), and verdict.
   - Below the prose: the full grounding table (sortable: verdict, type, company/source), a retracted-claims section (explicitly shown — retraction is the system working, sell it), and a stats strip: grounding rate for this run, cost, tokens, loop iterations, link to the Langfuse trace (tailnet-only; show the trace id regardless).

## Deployment

- Cloudflare Pages, connected to the GitHub repo, building `frontend/` on push to main (preview deploys on PRs for free). API calls go to the Phase 4 tunnel URL; set CORS on the FastAPI service for the Pages origin (backend change, one line, this phase).

## Tests / QA

- Component tests (Vitest + Testing Library) for the claim-span renderer (the one piece with real logic: mapping claims to text ranges, overlapping-span handling) against fixture payloads from the gold set.
- A fixture-mode flag (`VITE_MOCK=1`) that renders the result view from a canned JSON — makes UI iteration and screenshots independent of live runs.
- Cross-check the rendered grounding stats against the API stats (no client-side recomputation drift).

## Exit gate

- A stranger with the URL can ask an example question and, within one screen, *see* verification working: colored claims, hover-to-source, a retracted claim visible on at least one example.
- Lighthouse sanity (no egregious performance/a11y failures); looks credible on a laptop and acceptable on mobile.
- Screenshot/GIF of the grounding table saved under `docs/` (README + CV material).
