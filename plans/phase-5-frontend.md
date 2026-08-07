# Phase 5 — Frontend (The Grounding-Table Demo Surface)

**Goal (blueprint §8/§13):** a thin but excellent Vite + React UI on Cloudflare Pages whose centerpiece is **the per-claim grounding table** — the answer with each claim colour-coded by verdict, hover-to-reveal the exact source span. This single view makes the invisible verification work visible in three seconds; it is the highest-value hiring artifact in the project. Thin scope, high polish.

## Context (read this if starting cold)

Revised 2026-08-05, after Phase 4 deployed. The original version of this section described a service that was planned and not the one that exists; where the two differ, Phase 4 is right and this document was wrong.

Phases 0–4 delivered a FastAPI service **already running on the VPS**, answering real questions, with a nightly backup and a monthly drift eval on cron. It runs on the **private path**: bound to loopback, reachable over the tailnet only, `FIE_TURNSTILE_REQUIRED=false`, **no nginx vhost installed**. Making it public is this phase's job, and `deploy.sh` refuses to start with Turnstile off and a vhost present, so the two cannot drift apart.

Three corrections to what this plan used to assume:

- **There is no Cloudflare Tunnel.** Public exposure is the VPS's own nginx, on an `sslip.io` hostname, with `limit_req` and fail2ban in front (ADR 0035).
- **`X-API-Key` is not the security model.** On the public path the key *identifies*; it does not control — a static SPA ships its key in the bundle. **Turnstile is the enforcement**, together with a USD spend governor and a per-IP daily quota (ADR 0030). See "Turnstile" below; it is a work item, not a footnote.
- **A recorded run, not a live one, is what a visitor lands on** (ADR 0027, and decision 1 below).

### The contract, as built

Do not work from the sketch below. **Generate or hand-write TypeScript types from the Pydantic models** in `src/fie/api/models.py` and `src/fie/state/__init__.py`. The sketch is here to name the shapes, not to define them.

- `POST /v1/questions {question, companies?, turnstile_token?}` → `202 {job_id, status, source}`
- `GET /v1/jobs/{id}` → `JobStatusResponse` — `{status, question, companies, cost_usd, tokens, error, progress}`, where `progress` is `{step, phase, verify_iteration, critic_iteration, cost_usd, tokens}`.
  **`phase` is one of six literals** — `starting | researching | synthesizing | verifying | reviewing | finalizing` — derived from our own state channels rather than from LangGraph's task bookkeeping, deliberately, so a library upgrade cannot silently blank it. Render the six phases. There is no `current_node`.
- `GET /v1/jobs/{id}/result` → `RunPayload` = `{job_id, source, created_at, result}`, and `result` is `RunResult`:
  `{run_id, question, answer, sub_questions, grounding[], retracted[], verify_iterations, critic_iterations, critic_passed, budget, error, awaiting_review}`.
  **`grounding` and `retracted` are two lists, not one list with a status field.** Costs are in `budget`, not `stats`.
- Each row is a `GroundingRow`: `{claim_id, text, claim_type, status, verdict, method, supporting_span, feedback, sources[]}`. `sources` is a **list** of `SourceRef` `{chunk_id, doc_id, section, accession_no, filing_date}`.
- `GET /v1/jobs/{id}/trace` → `{trace_id}` — a **separate call**, not part of `RunPayload`.

`RunPayload` is documented in the code as the type Phase 5 renders for both a live run and a recorded one. Build one renderer.

## Decisions taken (2026-08-05)

1. **A recorded run is the landing page. Live ask ships too, but is not what the visitor lands on.** This is what ADR 0027 bought: `recorded/` holds the accepted baseline's 30 runs plus an `index.json`, in the exact `RunPayload` shape, so the CDN answers the reader and the VPS never sees the request. "Ask your own" is a deliberate second action.
   **Exclude run `2b11fa1d4f9e` from the landing rotation** — it delivers an answer with zero claims and renders as an empty grounding table, which inverts the thing being demonstrated. ADR 0038 is the finding and the fix.
2. **The human-in-the-loop review view is cut.** `API_REVIEW_MODE` is `off` in production: on a page with no login the reviewer is any visitor, and an abandoned review holds one of two concurrency slots forever (ADR 0034). The endpoints, the graph interrupt and their tests all stay; only the mode is off, so turning it on later is one environment variable. **Do not render an `awaiting_review` UI.** A job cannot reach that state with the mode off; treat it as an error state, not a screen.
3. **The hostname is `https://edgaragent.pages.dev`.** `API_CORS_ORIGINS` is set to it. **The same string is the hostname the Turnstile widget must be registered for.** A mismatch refuses every submission while CORS still passes, which reads as two unrelated faults. The name is only claimed when the Pages project is created; if `edgaragent` is taken, the string changes in both places at once.
4. **`recorded/` is regenerated locally, from `data/checkpoints.db`, once backend changes 1 and 2 land** — not from the pre-public-deploy VPS run, as ADR 0038 assumed. **None of the 30 committed files carries an `answer_span`**: they were exported before the field reached `GroundingRow`, so the headline view cannot render against `recorded/` as it stands, and blocking that on a 75-minute run buys nothing. The local checkpoints and `eval/reports/agent-20260801-122527.json` (30 run ids) are both present, and `export-runs` derives the payload through the same `build_run_result()` the live path uses. Drop `2b11fa1d4f9e` at export. Re-export a second time only if the VPS run is accepted as a new baseline.
5. **ADR 0038 §1 lands in this phase, before the VPS eval** — see backend change 3. That run is what supersedes the 29-verifiable-runs-of-30 baseline, and it should not be able to reproduce the hole it is superseding. §2 goes in with it; §3 is bookkeeping the run itself settles.
6. **A recorded run shows no trace id.** `GET /v1/jobs/{id}/trace` stays live-only and nothing is folded into `RunResult`. The 2026-08-01 baseline was produced on the developer's machine against the local Langfuse on port 3001, while production tracing is Langfuse Cloud (ADR 0032) — an exported `trace_id` would be an identifier no visitor can resolve. The stats strip says which it is looking at rather than showing a dead id. **Do not fetch the endpoint for a recorded run.**
7. **No preview deploys.** A preview lands on `<hash>.edgaragent.pages.dev` — a different origin from the one decision 3 pins `API_CORS_ORIGINS` to, and a different hostname from the one the Turnstile widget is registered for, so it would fail both in exactly the two-unrelated-faults way decision 3 warns about. Widening the allowlist to `*.edgaragent.pages.dev` is the wrong trade on a surface whose entire threat model is a copied API key.
8. **The full VPS eval moves to the end of the phase, and stays the gate.** It was going to lead, on the reasoning that it depends on none of the other steps — true, but it is also 75 minutes and $0.69 that buy nothing until the rest exists. ADR 0036 is untouched: it still runs *before* the public deploy, just as the last thing rather than the first. What it needed from this phase is already done — the image carrying ADR 0038 §1 is deployed on the private path (2026-08-05, revision `4084485`, verified with one uncached live question), so the run that supersedes the 29-verifiable-runs-of-30 baseline can no longer reproduce the hole it is superseding. **Nothing else in this plan waits on it.**

## Three backend changes this phase must make first

**All three landed on 2026-08-05** (`66634f7`, `3c72e04`, `4084485`) and are in the deployed image. They stay written out because they are why the payload carries the fields the views below assume, and because `recorded/` was regenerated against them.

1. **Put `answer_span` on `GroundingRow`.** `Claim.answer_span` already holds the verbatim sentence the claim came from, validated at synthesis as a substring of the answer and repaired by a bounded retry when it is not (ADR 0023). `build_run_result()` drops it, so **nothing in the payload locates a claim in the prose**. This is one field plus a test — and it is better than the char offsets this plan originally asked for, because a span survives the retraction edits that offsets would not.
2. **Add `ticker` and `form_type` to `SourceRef`.** The hover popover specifies company and form type. `SourceRef` carries neither. Both are already on the chunk — as **top-level fields on `EvidenceChunk`**, not entries in its `metadata` dict, which is where `build_run_result()` reads `accession_no` and `filing_date` from. Read them off the chunk directly: `chunk.metadata.get("ticker")` returns `""`.
3. **Implement ADR 0038 §1 and §2.** `synthesize` retries once on an empty claim list and sets `error` when the retry is also empty; `eval-agent` reports `runs_with_no_claims` beside the metrics, ungated. The ADR is the spec and is unimplemented. It belongs here rather than in a later phase because the VPS eval below is what supersedes the current baseline, and a gating run that can still deliver an unverified figure invisibly is not a gate. Retrieval, prompts and models stay cleared — this adds a structural check, it tunes nothing.

Each is small. Each needs `make check` green and a test, per the repo's normal rules. **Changes 1 and 2 must land before `recorded/` is regenerated** (decision 4), because the re-export is what puts the new fields into the static payloads.

The trace id was a fourth item in an earlier draft. It is now decision 6 and needs no backend change.

## What is built (2026-08-06)

`frontend/` exists and all four views are written: landing over
`recorded/index.json`, ask with the Turnstile widget, run status over the six
phases, and the result view. `make check` is green (255 tests) and
`npm run check` is green (20 tests, `tsc -b` clean, `vite build` clean).

Three things a cold start should not re-derive.

**The claim → prose mapping is `src/lib/answer.ts`, and its test asserts the
product's thesis**: across all 29 recorded runs, the highlighted text equals
the claim's `answer_span` exactly. Nothing guesses a position from word
overlap — a colour on a sentence no verifier ruled on is the one error this
surface must not make. Two failures are reported separately and neither is
guessed at: `spanless` (synthesis emitted no span — 6 rows, all in run
`4d264b627151`) and `unlocated` (the span exists but another claim's excision
took the sentence — 0 in this baseline, and it is ADR 0021 point 3, so it can
recur).

**Retracted spans are absent from the prose by design and are never searched
for.** Across the corpus, spans missing from the body equal the retracted-row
count exactly, in every run. Looking for them would report correct behaviour
as a fault.

**`recorded/` reaches the bundle as a committed symlink**,
`frontend/public/recorded → ../../recorded`, which Vite dereferences into
`dist/`. No copy step, so the export stays the only source of truth.
`export_runs()` now prunes run files it did not write on a full export
(`--run-ids` subsets never prune), which matters at the *second* export: run
ids change wholesale when a baseline is accepted.

**npm policy, set 2026-08-06**: `frontend/.npmrc` pins `ignore-scripts=true`
and `before=2026-07-23` — nothing published inside a two-week window is ever
resolved. Use `npm --prefix frontend run <script>`, never `npx`: npx silently
fetches from the network when a binary is missing from the local tree. The
date pin currently holds `postcss` at 8.5.22, which carries a moderate
advisory fixed in 8.5.25; that version becomes eligible on 2026-08-12.

Both of this section's open items closed on 2026-08-07: the screenshot is
`docs/grounding-table.png`, and everything under "Deployment" below is live.
What outlives the phase is at the foot of this file.

## Turnstile (the work item this plan used to be missing)

`POST /v1/questions` already accepts `turnstile_token`; the backend half is built and tested — `test_turnstile_refuses_a_submission_carrying_a_valid_api_key` is the load-bearing assertion. The frontend half does not exist.

- Embed the widget on the Ask view. Mint a token **per submission**, not per session.
- Send it as `turnstile_token`. Handle refusal as a first-class state with a retry, not a generic error toast.
- The site key is public and belongs in the bundle. The **secret** key goes into `secrets.enc.yaml` and never into the frontend.

## Stack & conventions

- `frontend/` in the monorepo: Vite + React + TypeScript, Tailwind. No heavyweight state library — polling plus React Query (or a small fetch hook) is enough. The corpus is fixed (TSLA/AAPL/NVDA) and runs are single-user demo flows.
- **When building, load the frontend design skill (`impeccable` / `frontend-design`)** — the blueprint sets an explicit design bar: a serious analyst tool, not a toy. Clean, confident, legible; restrained palette; strong typography.
- API base URL, API key and Turnstile site key via Vite env vars. All three are public in the bundle by design; the spend governor, the per-IP quota, Turnstile and nginx are the real backstop.

## Views (four, no more — the landing view is new, the review view is gone)

1. **Landing — a recorded run.** Read `recorded/index.json`, render one run through the same result view as a live one. This is the first thing a stranger sees, and it costs nothing to serve.
2. **Ask.** Question input with 3–4 example analyst questions as one-click chips, chosen from the gold set so they demo well; company scope selector; Turnstile widget; submit → the run view.
3. **Run status.** Poll `GET /v1/jobs/{id}` every ~2s: the six phases with the current one live, verify-iteration counter, running cost.
4. **Result — the killer view (blueprint §8).**
   - The answer rendered as prose with each claim's span colour-coded, located by `answer_span`.
   - **Hover (click on touch) a claim → popover:** the exact supporting span quoted, source metadata (company, form type, filing date, section, accession number), verification method (XBRL reconciliation / semantic / attribution), and verdict.
   - Below the prose: the full grounding table (sortable by verdict, type, company/source), a retracted-claims section (explicitly shown — retraction is the system working, sell it), and a stats strip: grounding rate for this run, cost, tokens, loop iterations, and the trace id **on live runs only** (decision 6).

### Three rendering traps, all confirmed in the data

**The answer already contains an appended retraction section.** `RETRACTED_HEADER = "### Retracted claims"` is part of the answer contract, and **6 of the 30 recorded answers carry it**. Split the answer on that header before rendering, or retracted claims appear twice.

**`accepted` must never be coloured as `grounded`.** `ClaimStatus` has six values — `pending | grounded | unsupported | partial | retracted | accepted`. ADR 0034 exists so a human waving a claim through is not laundered into `grounding_rate`; a palette that collapses the two defeats it in the UI, which is the one place a reader would believe it. With decision 2 in force `accepted` should not occur, so render it as a visible anomaly rather than as green.

**`status` and `verdict` are different fields.** `status` is the final word and carries `retracted` and `accepted`; `verdict` is the verifier's ruling and is one of three. Colour by `status`. Show `verdict` in the popover.

Overlapping spans are real, not hypothetical: one sentence can carry both a retracted and a grounded claim (ADR 0021, point 3, which survived ADR 0023 as predicted-extinct-but-not).

## Deployment, and the order it must happen in

Cloudflare Pages connected to the GitHub repo, building `frontend/` on push to `main`, with no preview deploys (decision 7). API calls go to **`https://api.example.com`** — the vhost in `deploy/nginx/fie.conf`, written in Phase 4 and not yet installed.

**The ordering is forced, because Turnstile cannot be configured before the hostname exists:**

```
Pages project → hostname confirmed → Turnstile widget for that hostname
→ turnstile_secret_key into secrets.enc.yaml → nginx vhost + certbot + fail2ban
→ FIE_TURNSTILE_REQUIRED back to its default (true)
→ full eval-agent on the VPS, passing → deploy
```

The eval sits last rather than first (decision 8) and is still what the public deploy waits on, per ADR 0036. Everything before it is reversible; the deploy that follows it is the one that lets a stranger spend money.

`deploy.sh` refuses Turnstile-off with a `fie` vhost present, so a wrong order fails loudly rather than shipping the service open. Reactivate the paused uptime monitor once the public hostname answers.

## Tests / QA

- Component tests (Vitest + Testing Library) for the claim-span renderer — the one piece with real logic: mapping claims to text ranges, overlapping-span handling, and the retraction-header split.
- **Fixtures are free.** `recorded/runs/*.json` is 30 canned payloads in the exact response shape. A `VITE_MOCK=1` flag should read those rather than invent anything.
- Cross-check the rendered grounding stats against the API payload — no client-side recomputation drift.

## Exit gate

- ✅ A stranger with the URL sees a recorded run immediately, and within one screen *sees* verification working: coloured claims, hover-to-source, a retracted claim visible on at least one example.
- ✅ Asking a live question works end to end, with Turnstile enforced (2026-08-07, through the real challenge in a browser).
- ☐ Lighthouse sanity (no egregious performance or a11y failures); credible on a laptop, acceptable on mobile.
- ✅ Screenshot/GIF of the grounding table saved under `docs/` (README + CV material) — `docs/grounding-table.png`.

### Inherited from Phase 4

Both are done — the burst test on 2026-08-07, the eval on 2026-08-06 (it
passed every target; `degraded_attempts: 1` is what stops it becoming a
baseline, not a failure). They stay written out because they are why the
public deploy was allowed to happen.

- **`./scripts/burst-test.sh` against the live host.** Genuinely blocked on the vhost this phase installs. It proves nginx refuses a burst rather than the app (both return 429; the response body tells them apart), that polling stays fast, and that **the five other production services on the box keep serving throughout**. These limits live above the application, so `make test` can never see them.
- **One full `eval-agent` on the VPS, passing against `eval/baselines.json`.** ADR 0036 makes this the gate before the public deploy. Run it with `./deploy/monthly-eval.sh` — there is no `uv` on that box and deliberately will not be. ~75 minutes, ~$0.69. **This one is not blocked on the vhost, and an earlier draft of this plan said it was.** `monthly-eval.sh` starts a one-off container on the `fie_default` network against the service's own volume and calls `answer_question()` directly; nginx is nowhere on that path. It is blocked on nothing at all, which is why decision 8 puts it last: it can be run at any moment, so it should be run at the moment its result is worth the most — over the image that ships. ADR 0038 §1 is already in that image.

Both were written as Phase 4 exit criteria and neither was doable in Phase 4. They are listed here so they are not forgotten.

## Open items (2026-08-07)

Two things outlive the build. Each has a trigger rather than a hope, because
"still open" without one is how an item stops being read. A third — the
budget uptime monitor — closed the same day it was written, on 2026-08-07.
The gaps that are open *by choice* rather than pending are in
`docs/monitoring.md` under "What is not watched".

- **The Lighthouse pass** — the one exit-gate bullet with nothing behind it.
  Chrome DevTools → Lighthouse, mobile preset, against
  `https://edgaragent.pages.dev/`, which serves the recorded landing view and
  therefore needs no live run and spends nothing. Deliberately not a
  dependency: `npx` is banned by the npm policy above, and a devDependency for
  a check run once is exactly the un-earned complexity `plans/00-overview.md`
  §3.1 refuses.
- **`postcss` 8.5.22 → 8.5.25**, eligible on **2026-08-12**, when the
  `before=2026-07-23` pin's fortnight clears the release. The advisory is
  moderate and build-time only — postcss runs in `vite build`, and nothing of
  it reaches the static bundle a visitor loads — which is why waiting the
  window out is the right trade rather than an exemption. Run
  `npm --prefix frontend audit` after, per the standing rule.
