# Phase 4 — Backend Service + Deployment

**Goal (blueprint §13):** FastAPI service, Docker, deploy to the VPS, GitHub Actions CI running tests + the eval set, real secrets management. This is the "shipped a production system, not a prototype" proof (blueprint §7). The human-in-the-loop gate from the blueprint topology also lands here, because it needs an API surface to exist.

## Context (read this if starting cold)

Phases 0–3 delivered the complete engine as a library + CLI: the full cyclic multi-agent graph over a Qdrant-backed corpus (TSLA/AAPL/NVDA EDGAR filings), XBRL-exact numeric verification, SQLite checkpointing, guardrails, Langfuse tracing (currently self-hosted via local docker compose), and a regression eval runner with committed baselines (`eval/baselines.json`). This phase wraps it in a service and ships it.

**Job-model decision (already made — `00-overview.md` §3.1):** in-process asyncio + polling, persisted in SQLite. No broker/queue. Upgrade trigger documented there.

### Measured environment (2026-08-03) — this decides most of the phase

Access is `ssh vps` over Tailscale. Inventory taken before planning, not assumed:

**`vps` — 2 vCPU, 3.7 GB RAM, 38 GB disk (72% used, 11 GB free), 2 GB swap untouched, no OOM history.** `nginx` already owns :80/:443. **Five other projects already run on this box** — `neighbour-c-rec`, `bot_wristshotai`, `artstamp`, `neighbour-a`, `neighbour-d` — so every decision here is constrained by not disturbing them.

| | limit | actual |
|---|---|---|
| neighbour-c Qdrant | 1.37 G | 940 MB |
| neighbour-c recommender | 600 M | 342 MB |
| neighbour-c postgres | 250 M | 43 MB |
| the four uncapped API containers | — | ~400 MB total |
| host (dockerd, nginx, tailscaled, …) | — | ~400 MB |
| **available** | | **1.8 GB** (≈700 MB if the capped containers reach their limits) |

**Consequence: the Langfuse self-host stack cannot go on this box** (six containers; ClickHouse alone wants 1–2 GB). See deliverable 2.

**Measured cost per run** — `run_budget` over the last 40 checkpointed runs in `data/checkpoints.db`, on the current GLM-synthesis arm: **$0.023 median**, $0.064 p90, $0.075 max, $0.007 min; 25k tokens median, 85k max. Two things follow. The operator's ceiling for the public demo is **$5/month ≈ 215 live questions**, which is a real budget rather than an anxiety. And the GLM arm is no longer only a cost A/B — reverting `synthesis`/`critic` to Sonnet roughly triples per-run cost and cuts that to ~70 questions/month. Record this in the Phase 7 cost note.

## Deliverables

### 1. FastAPI service (`src/fie/api/`)

- Async throughout (the graph already is). Versioned prefix `/v1`, typed Pydantic request/response models (reuse the Phase 1 grounding-table models — they are the contract).
- **Endpoints:**
  - `POST /v1/questions` `{question, companies?}` → `202 {job_id}`. Creates a SQLite job row (`id, status, question, created_at, thread_id`), launches an asyncio task running the graph (job registry with the concurrency semaphore from guardrails).
  - `GET /v1/jobs/{id}` → status: `queued|running|awaiting_review|succeeded|failed`, plus progress (current node, verify iteration — read from checkpoint metadata) and cost so far.
  - `GET /v1/jobs/{id}/result` → final answer + per-claim grounding table + run stats (cost, tokens, iterations, trace id).
  - **Human-in-the-loop gate (blueprint §4/§3 guardrails):** runs with unresolved claims after verification stop in `awaiting_review` (LangGraph interrupt before finalization). `GET /v1/jobs/{id}/review` lists unresolved claims; `POST /v1/jobs/{id}/review` `{claim_id, action: accept|retract}` resumes the graph from the checkpoint. Runs where everything grounded cleanly skip the gate (config flag to force it for demos).
  - `GET /healthz` (liveness + dependency checks: Qdrant, SQLite). **Langfuse is deliberately not a health dependency** now that it is Cloud — tracing is not on the answer path, and making an external SaaS able to turn the service red would page the operator for someone else's outage. Report it as an informational field, never in the pass/fail.
- **Auth:** static API key header (`X-API-Key`), constant-time compare. This authenticates the *tailnet* consumer (CLI, the operator's Mac) and nothing else. **A static SPA cannot hold a secret** — the Phase 5 frontend ships its key in the bundle, where it is readable in devtools, so on the public path the key is an identifier, not a control. Say this out loud in the ADR and in `docs/security.md`; what actually enforces the limit is the next bullet.

- **Abuse controls (the real enforcement on the public path).** Layered cheapest-first, and deliberately *outermost-layer-first* — the outer layers must not be code we wrote, because our code can be wrong:
  1. **Provider-side hard caps** (outside the repo, done first): a fixed OpenRouter balance with auto-top-up **off**, plus an Anthropic console spend limit + alert. This is the only layer that survives a bug in our own budget accounting or a leaked key.
  2. **Cloudflare edge** (free-tier allowances verified 2026-08-03): **Turnstile** on submit — free, unlimited challenges, ~1M siteverify calls/month, no login required. It kills drive-by scripts, the realistic threat for a portfolio site, and it is what makes the public API key worthless when copied. A rate-limiting rule sits alongside it as a **burst shield only**: the Free plan allows **1 rule, IP characteristic only, fixed 10-second counting window and 10-second mitigation**, expression limited to Path and Verified Bot. **It cannot express a daily quota** — so the per-IP quota below has to live in application code, not at the edge. Do not plan around the edge rule doing more than absorbing floods.
  3. **Global spend governor**: a daily+monthly USD counter in SQLite fed by the same `BudgetTracker` that already reports per-run cost. `POST /v1/questions` returns `429` when either is spent. Set **$5/month with a ~$0.35/day sub-cap**, so one bad day costs 7% of the month rather than all of it.
  4. **Per-IP daily quota** (3 questions/IP/day is generous for a demo) and a **Turnstile token required per submission** — this is what makes the public API key worthless when copied.
  5. **Concurrency semaphore + queue-depth cap** in the job registry (2–3 concurrent runs): the OOM control, sized to the RAM table above. Return `429` when the queue is full rather than accepting unbounded jobs.
  6. **Exact-match answer cache** keyed on normalized question + company scope. Demo questions cluster hard, so the tenth visitor asking about Apple's FY2025 revenue costs $0. **Exact match only** — a semantic cache can serve the answer to a *different* question, which for a verification system is the worst failure mode available.
  7. **Input constraints**: question length cap, ticker allow-list (the corpus is only TSLA/AAPL/NVDA). First edge of the Phase 6 sanitization story.

  **No Cloudflare Worker / BFF proxy.** It was considered: a Worker would hold the API key server-side so it never reaches the browser. Rejected because Turnstile already makes a copied key useless, so the Worker's one unique win is worth little, and it costs a second runtime, a second deploy pipeline, and a new language — exactly the un-earned complexity `00-overview.md` §3.1 bans. **Upgrade trigger:** observed key-copying abuse that Turnstile + per-IP quota do not absorb → move quota enforcement to a Worker then, not now. Record in the ADR.

- **Config changes this phase needs** (`src/fie/config.py`):
  - A **public-path per-run cost cap**. `budget_max_cost_usd: 5.0` is ~200× the $0.023 median — a sane runaway guard for a CLI the operator drives themselves, but not a control for an endpoint strangers can reach. ~$0.25 makes it one; the tailnet path keeps the loose cap.
  - A **shorter public-path node timeout**. `node_timeout_seconds: 600.0` interacts badly with a 2–3 slot semaphore: CLAUDE.md records that synthesize stalls at the cap ~1.5% of the time, so one stall parks a slot for ten minutes. Either shorten it on the public path or size the semaphore knowing it.
- **Startup recovery:** on boot, jobs stuck in `running` (process died) are marked `interrupted` and are resumable from their LangGraph checkpoint via a `POST /v1/jobs/{id}/resume` endpoint — this is where decision 3.4 pays off.
- **Recorded-run export** (`fie export-runs`): read completed runs out of `data/checkpoints.db` and emit the exact `GET /v1/jobs/{id}/result` payload as static JSON. Phase 5 ships these as the default demo surface — instant, free, works when the VPS doesn't, and a *live* demo that takes minutes and stalls 1.5% of the time is not what a recruiter clicking a link will wait for. Live runs stay a deliberate second action behind the abuse controls. Label recorded vs. live honestly in the UI; a recorded run showing a **retracted** claim is the single best demonstration of what this system does. The 30 gold questions are already sitting in the checkpoint DB.

- Structured logging (`structlog`, JSON in prod), request ids correlated with Langfuse trace ids.

### 2. Containerization

- Multi-stage Dockerfile (uv build → slim runtime, non-root user). Images pushed to GHCR.
- **Langfuse does not go on the VPS.** The plan originally said to migrate the dev self-host stack there; the measured inventory says no. Six containers (Postgres, ClickHouse, MinIO, Redis, web, worker) against 1.8 GB available would not just risk the demo, it would risk OOM-killing five running production services. **Production traces go to Langfuse Cloud's free tier**; `docker-compose.dev.yml` stays exactly as it is for local development. Free-tier allowance checked by the operator 2026-08-03 and confirmed sufficient — at ~200 runs/month it is not close to binding. No trace export/import from dev; fresh start is fine. **ADR** — this is a measured reversal, not an oversight, and reads as engineering: planned, measured the target, didn't fit, decided, recorded the upgrade trigger (a bigger box, or a second cheap VPS dedicated to Langfuse, if self-hosting becomes worth it).
- `docker-compose.prod.yml` for the VPS: `fie-api`, `fie-qdrant`, `cloudflared` — pinned versions, restart policies, volumes on the VPS disk. One named compose project (`fie`) so it coexists with the five other projects.
- **Every container gets an explicit `mem_limit`** — this box has no room for an unbounded one, and four of the existing containers are already uncapped:

  | | limit | why |
  |---|---|---|
  | `fie-api` | ~640 MB | the `.venv` is 597 MB and pulls `onnxruntime` (via `fastembed`, needed at query time for the sparse BM25 vector). Biggest lever on RSS if this proves tight. |
  | `fie-qdrant` | ~384 MB | the corpus is 4,181 points × 1536 dims ≈ 26 MB of vectors — generous. |
  | `cloudflared` | ~64 MB | |

  ~1.1 GB against 1.8 GB available, with 2 GB of untouched swap as backstop. It fits **only because Langfuse isn't there**.
- **Run a second Qdrant rather than reusing neighbour-c's.** Reuse would save ~150 MB of process baseline and the collections are already namespaced (`fie_chunks__*`), but that instance is at 67% of its limit and an ingest run in a demo project must not be able to affect a production service. 150 MB is a fair price for that isolation, and it matches the "isolate under its own compose project" rule.
- Disk: ~1.6 GB needed (image + parsed data + chunks + Qdrant storage + SQLite) against 11 GB free. `docker builder prune` reclaims 2.2 GB of stale build cache now if headroom is wanted. Configure log rotation before deploying, not after — 11 GB free is not a lot.
- Persistent data (SQLite files, Qdrant storage) on named volumes; a nightly `cron` tar-backup of volumes to a local backup dir (cheap, honest; offsite backup noted as out of scope).

### 3. Exposure & networking

- **The admin surface stays Tailscale-only** (bind to the tailnet interface / use Tailscale serve) — least exposure by default. Langfuse is no longer part of this; its UI is Langfuse Cloud.
- The Phase 5 frontend on Cloudflare Pages needs public API reachability: expose **only the API** via **Cloudflare Tunnel** (`cloudflared` container in the compose; no open inbound ports on the VPS, TLS handled by Cloudflare, fits the existing Cloudflare stack). The inventory confirms this: **`nginx` already owns :80/:443** for the five other projects, and a tunnel needs no inbound port and no edit to an nginx config those services depend on. Record as an ADR (alternatives: Caddy + open 443 — rejected: more surface; a new nginx vhost — rejected: touches shared config for five running services).

### 4. Secrets (blueprint §7: "a real secrets manager, not env files in the repo")

**Decision: SOPS + age for storage, compose secrets for runtime injection.** Confirmed by the operator 2026-08-03 (they do not use 1Password, so `op run`-style injection is out).

- **Storage — SOPS + age.** Encrypted `secrets.enc.yaml` committed to the repo, decrypted only at deploy time on the VPS (age key on the VPS + the operator's Mac, never in git). Secrets get version history, rotation becomes a visible commit, there is no SaaS dependency and no runtime dependency that can take a deploy down. **Back the age private key up in a password manager, not only on two machines that can both fail** — losing it loses everything.
- **Runtime — compose secrets, not env vars.** Env vars leak into `docker inspect`, crash dumps, child processes, and any library that dumps `environ`. `pydantic-settings` supports `secrets_dir` natively, so `config.py` takes a small change, not a rewrite. For a project whose Phase 6 is a security writeup this is the better artifact.
- **The most sensitive value here is `EDGAR_IDENTITY`**, not the API keys — it is personal data, and the repo history has been scrubbed of it once already.
- In CI, secrets come from GitHub Actions encrypted secrets. Never baked into images.
- For the "no secret in history / image layer / CI log" gate item, name the tools: `gitleaks` or `trufflehog` over full history in CI, `docker history --no-trunc` on the built image, and note that GH Actions masking **fails open on transformed values** (a base64'd secret is not masked).
- ADR with alternatives: plain `.env` on the VPS (rejected: no history, no recovery if the box dies, demonstrates nothing); Infisical/Vault/Doppler (rejected: overkill for one VPS and ~6 secrets, and each adds a runtime dependency that can break a deploy).

### 5. CI/CD (GitHub Actions)

**No live eval on PRs. The eval-smoke-subset gate the blueprint implies was specced and rejected here — the subset is the problem, not the frequency.** Three measured reasons, all checked against the code on 2026-08-03:

- `--limit N` takes `questions[:N]`, and `expected_fact_coverage` iterates the list it is handed. The first 8 gold questions carry **8 of the 28** XBRL labels, so a subset run's coverage is measured over a *different population* than the `1.000` in `baselines.json`. Comparing them is the same category error CLAUDE.md already records for `citation_accuracy` — the population moved, not the per-class rates.
- The first 8 contain **0 of the 4 unanswerable questions** — the ones that test whether the system declines instead of confabulating when the corpus has no answer. A gate blind to that regression class is blind to the project's entire reason for existing.
- The denominators are too small to gate on. The accepted baseline scored 217 rows over 30 questions, so ~58 over 8: one unsupported claim is 0.017 against a 0.02 target, two fails the build. It would flip on noise, get re-run reflexively, and a gate you re-run reflexively is worse than none — it manufactures false assurance.

Note also that `on: pull_request` fires on `synchronize`, i.e. **every push to a branch with an open PR** — not only at merge. Any live-key job on that trigger runs far more often than it looks.

**So: run the full set, rarely, at the moments the answer matters.** At $0.023/question a full 30-question run is **$0.69** (agent spend from `run_budget` only — this **excludes the Sonnet `eval_judge` calls**, so treat it as a floor). Budget CI separately from the $5 demo cap.

| trigger | what runs | cost |
|---|---|---|
| every push / PR | `ruff` → `mypy` → `pytest` — free, deterministic, **no API keys in the workflow at all** | $0 |
| release tag (pre-deploy) | **full** `eval-agent`, gated against `eval/baselines.json` | $0.69 |
| monthly schedule | **full** `eval-agent`, report as workflow artifact; failure = alert | $0.69 |
| `workflow_dispatch` | full run on demand | $0.69 |

Two things this buys beyond the money. The path that runs ~100× more often now holds **no secrets at all**, which very nearly satisfies the "no secret in CI logs" exit item by construction. And the monthly run is reframed: with nothing in the repo changed, it measures what a PR gate structurally cannot — **external drift** (OpenRouter silently re-routing `z-ai/glm-5.2` to another provider or quantization, a `-lite` alias updated underneath us, a model deprecated, the embedding endpoint shifting). That is why it is monthly rather than weekly: it is watching for drift we cannot cause.

`uv run eval-agent --limit N --skip-judge` stays what CLAUDE.md already calls it — a **local** iteration tool, never a gate.

Considered and rejected: recorded-cassette replay of real model responses to give a behavioral check on every push for $0. It catches only plumbing regressions (the responses are frozen by construction, so quality drift is invisible), and `StructuredFakeModel` in `tests/fakes.py` already covers most of that ground. Real work, narrow gain.
- **Deploy (on tag / manual dispatch):** build + push image to GHCR → connect to the tailnet via the official Tailscale GitHub Action (ephemeral node) → `ssh` to VPS → `docker compose pull && docker compose up -d` → post-deploy smoke test (`healthz` + one canned question).
- The repo needs to be on GitHub by this phase (create it in Phase 0 in practice; CI config lands here).

### 6. Monitoring (blueprint §7: health, error rates, cost per run, latency)

- Langfuse **Cloud** dashboards for cost/latency/error-rate per run and per node (data's already there — this is configuration + a `docs/` note on what to watch).
- Uptime: a simple external ping on `healthz` via the tunnel URL, alerting to email/ntfy. **Use a free external service, not an Uptime-Kuma container** — that is another ~150 MB on a box with 1.8 GB available, spent to monitor a service the same box is running. An external prober is also the only one that can tell you the box is down.
- **Spend monitoring**: expose the governor's day/month totals on an admin endpoint and alert before the cap, not on it. The provider-side hard caps (deliverable 1) are the backstop, but hitting them silently means the demo is down.
- Log rotation configured for containers **before deploying** — see the disk note in deliverable 2.

## Tests

- API unit tests (`httpx` test client, graph mocked): job lifecycle, HITL review round-trip, resume-after-interrupt, authz, input validation (question length caps, ticker allow-list — first line of the Phase 6 sanitization story).
- **Abuse-control tests, all offline** — these guard real money, so they get the same rigor as the graph invariants: the spend governor returns `429` once the day/month cap is crossed and *stays* closed; per-IP quota counts per IP and resets on the day boundary; a submission without a valid Turnstile token is refused **even with a correct `X-API-Key`** (this is the assertion that encodes "the SPA key is not a control"); the semaphore admits at most N concurrent runs and the queue-depth cap returns `429` rather than queueing without bound; the cache returns a hit only on an exact normalized match and never across differing company scope.
- Recorded-run export: the emitted JSON validates against the same Pydantic response model the live endpoint returns, so Phase 5 renders both from one contract.
- One live end-to-end against the deployed VPS instance (manual gate in CI).

## Exit gate

- `curl` from the operator's Mac (tailnet) and from the tunnel URL: submit a question → poll → retrieve a verified answer with grounding table; a run with unresolved claims stops at `awaiting_review` and completes after a review action.
- CI green on the free checks (`ruff`/`mypy`/`pytest`) for every push, and **one full `eval-agent` run passing its gate against `eval/baselines.json` on the release tag** — that is the "agents still reliable" evidence, not a PR-time subset. Deploy workflow ships to the VPS end-to-end; **Langfuse Cloud** shows the production traces.
- No secret exists in the repo history, in an image layer, or in CI logs (check all three).
- **The box is still healthy with `fie` on it**: `free -h` shows headroom, no OOM events in `dmesg`, and **all five pre-existing projects still up** — that last one is a hard gate, not a courtesy.
- **The spend governor is proven against the real service, not only in tests**: drive the day cap down to a few cents, submit until it trips, confirm `429` and that the provider-side caps were never approached.
- ADRs: job model (confirming 3.1 as implemented), **Langfuse hosting (the measured reversal)**, tunnel exposure, SOPS secrets + compose-secrets injection, **public-demo abuse controls (including why no Cloudflare Worker, and what the free-tier edge rule can and cannot do)**, **Qdrant instance isolation**, **CI eval cadence (why the smoke subset is not a gate, and why the schedule is monthly drift detection)**, HITL gate semantics.
