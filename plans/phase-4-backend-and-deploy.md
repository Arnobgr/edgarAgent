# Phase 4 — Backend Service + Deployment

**Goal (blueprint §13):** FastAPI service, Docker, deploy to the VPS, GitHub Actions CI running tests + the eval set, real secrets management. This is the "shipped a production system, not a prototype" proof (blueprint §7). The human-in-the-loop gate from the blueprint topology also lands here, because it needs an API surface to exist.

## Context (read this if starting cold)

Phases 0–3 delivered the complete engine as a library + CLI: the full cyclic multi-agent graph over a Qdrant-backed corpus (TSLA/AAPL/NVDA EDGAR filings), XBRL-exact numeric verification, SQLite checkpointing, guardrails, Langfuse tracing (currently self-hosted via local docker compose), and a regression eval runner with committed baselines (`eval/baselines.json`). This phase wraps it in a service and ships it.

**Environment facts:** the target is the operator's **existing VPS**, reachable over their **Tailscale network**; they provide the auth command for access when this phase starts — ask for it first. Other services may already run on the VPS: inventory before touching anything, and isolate this project under its own compose project + directory.

**Job-model decision (already made — `00-overview.md` §3.1):** in-process asyncio + polling, persisted in SQLite. No broker/queue. Upgrade trigger documented there.

## Deliverables

### 1. FastAPI service (`src/fie/api/`)

- Async throughout (the graph already is). Versioned prefix `/v1`, typed Pydantic request/response models (reuse the Phase 1 grounding-table models — they are the contract).
- **Endpoints:**
  - `POST /v1/questions` `{question, companies?}` → `202 {job_id}`. Creates a SQLite job row (`id, status, question, created_at, thread_id`), launches an asyncio task running the graph (job registry with the concurrency semaphore from guardrails).
  - `GET /v1/jobs/{id}` → status: `queued|running|awaiting_review|succeeded|failed`, plus progress (current node, verify iteration — read from checkpoint metadata) and cost so far.
  - `GET /v1/jobs/{id}/result` → final answer + per-claim grounding table + run stats (cost, tokens, iterations, trace id).
  - **Human-in-the-loop gate (blueprint §4/§3 guardrails):** runs with unresolved claims after verification stop in `awaiting_review` (LangGraph interrupt before finalization). `GET /v1/jobs/{id}/review` lists unresolved claims; `POST /v1/jobs/{id}/review` `{claim_id, action: accept|retract}` resumes the graph from the checkpoint. Runs where everything grounded cleanly skip the gate (config flag to force it for demos).
  - `GET /healthz` (liveness + dependency checks: Qdrant, SQLite, Langfuse reachability).
- **Auth:** static API key header (`X-API-Key`), constant-time compare. Enough for a single-consumer demo service; note as a decision.
- **Startup recovery:** on boot, jobs stuck in `running` (process died) are marked `interrupted` and are resumable from their LangGraph checkpoint via a `POST /v1/jobs/{id}/resume` endpoint — this is where decision 3.4 pays off.
- Structured logging (`structlog`, JSON in prod), request ids correlated with Langfuse trace ids.

### 2. Containerization

- Multi-stage Dockerfile (uv build → slim runtime, non-root user). Images pushed to GHCR.
- `docker-compose.prod.yml` for the VPS: `fie-api`, `qdrant`, the Langfuse self-host stack (migrated from the dev compose; export/import of dev traces not required — fresh start is fine, note it), each with pinned versions, restart policies, volumes on the VPS disk, and resource limits. One named compose project (`fie`) so it coexists with whatever else runs on the VPS.
- Persistent data (SQLite files, Qdrant storage, Langfuse DBs) on named volumes; a nightly `cron` tar-backup of volumes to a local backup dir (cheap, honest; offsite backup noted as out of scope).

### 3. Exposure & networking

- **API + Langfuse UI stay Tailscale-only** (bind to the tailnet interface / use Tailscale serve) — least exposure by default.
- The Phase 5 frontend on Cloudflare Pages needs public API reachability: expose **only the API** via **Cloudflare Tunnel** (`cloudflared` container in the compose; no open inbound ports on the VPS, TLS handled by Cloudflare, fits the existing Cloudflare stack). Record as an ADR (alternatives: Caddy + open 443 — rejected: more surface).

### 4. Secrets (blueprint §7: "a real secrets manager, not env files in the repo")

**Recommendation: SOPS + age.** Encrypted `secrets.enc.yaml` committed to the repo, decrypted only at deploy time on the VPS (age key lives on the VPS + the operator's Mac, never in git), rendered to the compose env. Auditable, no SaaS dependency, standard tooling. In CI, secrets come from GitHub Actions encrypted secrets. Docker containers read env injected by compose — never baked into images. ADR with alternatives (Infisical/Vault: overkill for one VPS).

### 5. CI/CD (GitHub Actions)

- **On PR/push:** `ruff` → `mypy` → `pytest` (all mocked, no keys) → **eval smoke subset** (~8 gold questions, live keys from GH secrets, cost-capped) with regression check against `eval/baselines.json`. Green build = code correct *and* agents still reliable (blueprint §6).
- **Nightly (scheduled):** full `eval-agent` run, report posted as workflow artifact; failure = alert.
- **Deploy (on tag / manual dispatch):** build + push image to GHCR → connect to the tailnet via the official Tailscale GitHub Action (ephemeral node) → `ssh` to VPS → `docker compose pull && docker compose up -d` → post-deploy smoke test (`healthz` + one canned question).
- The repo needs to be on GitHub by this phase (create it in Phase 0 in practice; CI config lands here).

### 6. Monitoring (blueprint §7: health, error rates, cost per run, latency)

- Langfuse dashboards for cost/latency/error-rate per run and per node (data's already there — this is configuration + a `docs/` note on what to watch).
- Uptime: a simple external ping on `healthz` via the tunnel URL (e.g. an Uptime-Kuma container on the VPS or a free uptime service) alerting to email/ntfy.
- Log rotation configured for containers.

## Tests

- API unit tests (`httpx` test client, graph mocked): job lifecycle, HITL review round-trip, resume-after-interrupt, authz, input validation (question length caps — first line of the Phase 6 sanitization story).
- One live end-to-end against the deployed VPS instance (manual gate in CI).

## Exit gate

- `curl` from the operator's Mac (tailnet) and from the tunnel URL: submit a question → poll → retrieve a verified answer with grounding table; a run with unresolved claims stops at `awaiting_review` and completes after a review action.
- CI green including the eval smoke subset; deploy workflow ships to the VPS end-to-end; Langfuse on the VPS shows the production traces.
- No secret exists in the repo history, in an image layer, or in CI logs (check all three).
- ADRs: job model (confirming 3.1 as implemented), tunnel exposure, SOPS secrets, HITL gate semantics.
