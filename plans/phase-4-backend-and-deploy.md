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

**nginx and exposure facts (measured 2026-08-03) — these decide deliverable 3.** nginx 1.24.0, `worker_processes auto` on 2 cores with `worker_connections 768`, so **~1536 connections are shared by all six vhosts**. That pool, not RAM, is what a flood exhausts first. `include /etc/nginx/conf.d/*.conf` sits in the `http` block and **`conf.d` is empty**, so http-level rate-limit zones land in a *new* file and no existing config is edited. `fail2ban` is active with only an `sshd` jail, so an nginx jail is a drop-in. certbot renews four certs on schedule, three of them on `sslip.io`. And **`sslip.io` is not on the Public Suffix List** (checked against publicsuffix.org) — Let's Encrypt therefore treats it as a single registered domain, which matters for *new* issuance only. No domain is owned on Cloudflare: `neighbour-c.example` uses third-party nameservers, and the other four projects use `sslip.io` off the raw IP.

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
- **CORS:** the frontend is served from `*.pages.dev` and the API from `api.example.com`, so every browser call is cross-origin. Allow exactly the Pages origin, not `*`.

- **Abuse controls (the real enforcement on the public path).** Layered cheapest-first, and deliberately *outermost-layer-first* — the outer layers must not be code we wrote, because our code can be wrong:
  Deliverable 3 puts the API behind the VPS's own nginx with no proxy in front of it, so **layers 2 and 4 are what replaces a CDN edge and they are not optional**.

  1. **Provider-side hard caps** (outside the repo, done first): a fixed OpenRouter balance with auto-top-up **off**, plus an Anthropic console spend limit + alert. This is the only layer that survives a bug in our own budget accounting or a leaked key.
  2. **Keep the traffic off the box entirely.** Recorded runs are static JSON and are the default demo surface, so they ship **inside the Cloudflare Pages bundle** rather than being served from the VPS. Cloudflare's CDN answers the visitor who only reads; the VPS never sees the request. Most visitors never start a live run, so this removes more load than every limit below combined, and it costs nothing.
  3. **Turnstile on submit** — free, unlimited challenges, ~1M siteverify calls/month. It works on origins Cloudflare does not proxy, so **no zone and no domain are required**; the registered hostname is where the widget renders (`*.pages.dev`), not the API host. It kills drive-by scripts, the realistic threat for a portfolio site, and it is what makes the public API key worthless when copied.
  4. **nginx `limit_req` / `limit_conn`, plus a fail2ban jail.** This is the load control, and under deliverable 3 it is ours to operate rather than rented. Two request zones because the paths have opposite profiles — `POST /v1/questions` is rare and expensive (`rate=3r/m`, `burst=2 nodelay`), `GET /v1/jobs/*` is frequent and cheap (`rate=60r/m`, generous burst) because the frontend polls. Also `limit_conn` 4 per IP; `limit_req_status`/`limit_conn_status` set to `429` so nginx and the API agree on the contract; `client_max_body_size 8k` and 10–15s client timeouts, since connections are the scarce resource at ~1536 shared and slow clients park them for free otherwise.

     **`limit_conn $server_name 100` is the one that protects the other five projects** — it bounds fie's share of that shared pool so a flood on the demo cannot starve `rec.neighbour-c.example`. Zones live in a new `conf.d` file (~21 MB of shared memory, ~160k IPs); no existing nginx file is edited.

     The fail2ban jail reads `limiting requests, excess:` from the fie error log and moves a persistent abuser from nginx (userspace, cost per request) to nftables (kernel, cost per packet). **Accepted cost: a firewall ban is IP-wide and therefore also blocks that address from the other five services.** Short `bantime` (1h) and a high threshold are the price of one shared IP and port.
  5. **Global spend governor**: a daily+monthly USD counter in SQLite fed by the same `BudgetTracker` that already reports per-run cost. `POST /v1/questions` returns `429` when either is spent. Set **$5/month with a ~$0.35/day sub-cap**, so one bad day costs 7% of the month rather than all of it.
  6. **Per-IP daily quota** (3 questions/IP/day is generous for a demo) and a **Turnstile token required per submission** — this is what makes the public API key worthless when copied.
  7. **Concurrency semaphore + queue-depth cap** in the job registry (2–3 concurrent runs): the OOM control, sized to the RAM table above. Return `429` when the queue is full rather than accepting unbounded jobs.
  8. **Exact-match answer cache** keyed on normalized question + company scope. Demo questions cluster hard, so the tenth visitor asking about Apple's FY2025 revenue costs $0. **Exact match only** — a semantic cache can serve the answer to a *different* question, which for a verification system is the worst failure mode available.
  9. **Input constraints**: question length cap, ticker allow-list (the corpus is only TSLA/AAPL/NVDA). First edge of the Phase 6 sanitization story.

  **Layers 2–4 cap load; layers 5–9 cap spend.** They are different problems and neither substitutes for the other: the governor stops a determined user from costing money, and it does so *after* FastAPI has accepted the connection.

  **No Cloudflare Worker / BFF proxy.** It was considered: a Worker would hold the API key server-side so it never reaches the browser. Rejected because Turnstile already makes a copied key useless, so the Worker's one unique win is worth little, and it costs a second runtime, a second deploy pipeline, and a new language — exactly the un-earned complexity `00-overview.md` §3.1 bans. **Upgrade trigger:** observed key-copying abuse that Turnstile + per-IP quota do not absorb → move quota enforcement to a Worker then, not now. Record in the ADR.

- **Config changes this phase needs** (`src/fie/config.py`):
  - A **public-path per-run cost cap**. `budget_max_cost_usd: 5.0` is ~200× the $0.023 median — a sane runaway guard for a CLI the operator drives themselves, but not a control for an endpoint strangers can reach. ~$0.25 makes it one; the tailnet path keeps the loose cap.
  - A **shorter public-path node timeout**. `node_timeout_seconds: 600.0` interacts badly with a 2–3 slot semaphore: CLAUDE.md records that synthesize stalls at the cap ~1.5% of the time, so one stall parks a slot for ten minutes. Either shorten it on the public path or size the semaphore knowing it.
- **Startup recovery:** on boot, jobs stuck in `running` (process died) are marked `interrupted` and are resumable from their LangGraph checkpoint via a `POST /v1/jobs/{id}/resume` endpoint — this is where decision 3.4 pays off.
- **Recorded-run export** (`fie export-runs`): read completed runs out of `data/checkpoints.db` and emit the exact `GET /v1/jobs/{id}/result` payload as static JSON. **The files ship inside the Pages bundle and are never served from the VPS** — that is abuse-control layer 2. Phase 5 ships these as the default demo surface — instant, free, works when the VPS doesn't, and a *live* demo that takes minutes and stalls 1.5% of the time is not what a recruiter clicking a link will wait for. Live runs stay a deliberate second action behind the abuse controls. Label recorded vs. live honestly in the UI; a recorded run showing a **retracted** claim is the single best demonstration of what this system does. The 30 gold questions are already sitting in the checkpoint DB.

- Structured logging (`structlog`, JSON in prod), request ids correlated with Langfuse trace ids.

### 2. Containerization

- Multi-stage Dockerfile (uv build → slim runtime, non-root user). Images pushed to GHCR.
- **Langfuse does not go on the VPS.** The plan originally said to migrate the dev self-host stack there; the measured inventory says no. Six containers (Postgres, ClickHouse, MinIO, Redis, web, worker) against 1.8 GB available would not just risk the demo, it would risk OOM-killing five running production services. **Production traces go to Langfuse Cloud's free tier**; `docker-compose.dev.yml` stays exactly as it is for local development. Free-tier allowance checked by the operator 2026-08-03 and confirmed sufficient — at ~200 runs/month it is not close to binding. No trace export/import from dev; fresh start is fine. **ADR** — this is a measured reversal, not an oversight, and reads as engineering: planned, measured the target, didn't fit, decided, recorded the upgrade trigger (a bigger box, or a second cheap VPS dedicated to Langfuse, if self-hosting becomes worth it).
- `docker-compose.prod.yml` for the VPS: `fie-api`, `fie-qdrant` — pinned versions, restart policies, volumes on the VPS disk. One named compose project (`fie`) so it coexists with the five other projects. No `cloudflared`: deliverable 3 exposes the API through the nginx already on the box.
- **Every container gets an explicit `mem_limit` *and* a CPU limit.** This box has no room for an unbounded one, four of the existing containers are already uncapped, and **there are only 2 vCPU** — memory limits alone would still let a runaway starve five production services of CPU:

  | | `mem_limit` | `cpus` | why |
  |---|---|---|---|
  | `fie-api` | ~640 MB | 1.0 | the `.venv` is 597 MB and pulls `onnxruntime` (via `fastembed`, needed at query time for the sparse BM25 vector). Biggest lever on RSS if this proves tight. One core leaves one for everyone else. |
  | `fie-qdrant` | ~384 MB | 0.5 | the corpus is 4,181 points × 1536 dims ≈ 26 MB of vectors — generous. |

  ~1.0 GB against 1.8 GB available, with 2 GB of untouched swap as backstop. It fits **only because Langfuse isn't there**. Set `pids_limit` too.
- **Run a second Qdrant rather than reusing neighbour-c's.** Reuse would save ~150 MB of process baseline and the collections are already namespaced (`fie_chunks__*`), but that instance is at 67% of its limit and an ingest run in a demo project must not be able to affect a production service. 150 MB is a fair price for that isolation, and it matches the "isolate under its own compose project" rule.
- Disk: ~1.6 GB needed (image + parsed data + chunks + Qdrant storage + SQLite) against 11 GB free. `docker builder prune` reclaims 2.2 GB of stale build cache now if headroom is wanted. Configure log rotation before deploying, not after — 11 GB free is not a lot.
- Persistent data (SQLite files, Qdrant storage) on named volumes; a nightly `cron` tar-backup of volumes to a local backup dir (cheap, honest; offsite backup noted as out of scope).

### 3. Exposure & networking

- **The admin surface stays Tailscale-only** (bind to the tailnet interface / use Tailscale serve) — least exposure by default. Langfuse is no longer part of this; its UI is Langfuse Cloud.
- **Decision: the public API is `https://api.example.com`, served by the nginx already on the box.** No domain is purchased and no Cloudflare zone exists, so Cloudflare Tunnel is unavailable (a named tunnel hostname requires a zone) and was dropped along with its `cloudflared` container. This matches the pattern `neighbour-a` and `neighbour-d` already use here, with tooling — nginx, certbot, fail2ban — the operator already operates four times on this box.

  **It is additive, not a shared-config edit.** One new file in `conf.d` for the rate-limit zones (currently empty, included from the `http` block) and one new file in `sites-enabled` for the vhost. No existing nginx file is modified; `nginx -t` gates a graceful reload.

  **Three accepted costs, recorded so they are decisions and not oversights:**
  1. **The hostname embeds the machine's IP.** If the box is rebuilt, migrated, or reassigned an address, every published link dies — README, CV, anywhere it was pasted. Accepted for now; it is the strongest argument for buying a domain later.
  2. **First issuance competes globally.** `sslip.io` is not on the Public Suffix List, so Let's Encrypt counts it as one registered domain and the 50-new-certs-per-week limit is shared with every sslip.io user. *Renewals are exempt*, so the three existing certs are safe, but issuing `fie.…` can fail for reasons unrelated to us, with no remedy but waiting. Plan for a retry; do not schedule the deploy against a hard deadline.
  3. **Nothing sits in front of the box.** Traffic lands directly on the shared nginx and the 2 vCPU. This is why abuse-control layers 2 and 4 exist, and why the exit gate proves them against the running service rather than only in tests.

  **Upgrade trigger:** sustained volumetric abuse that nginx + fail2ban do not absorb, or a change of IP — then buy a domain (Cloudflare Tunnel) or enable **Tailscale Funnel**, which needs no domain, no open port, and no `cloudflared`. Funnel was the alternative and remains the cheapest escape hatch: `tailscaled` 1.98.10 already runs here and MagicDNS is on, so it needs only the tailnet HTTPS-certificates toggle and the `funnel` node attribute in the ACL. It was not chosen because a `*.ts.net` hostname is no more permanent than the IP one and it puts the demo's availability under Tailscale's Funnel throttling policy. Record all of this in the exposure ADR (also rejected: Caddy + open 443 — more surface, duplicates a working nginx; moving `neighbour-c.example`'s nameservers to Cloudflare — repoints DNS for a live production service to serve a demo).

### 4. Secrets (blueprint §7: "a real secrets manager, not env files in the repo")

**Decision: SOPS + age for storage, compose secrets for runtime injection.** Confirmed by the operator 2026-08-03 (they do not use 1Password, so `op run`-style injection is out).

- **Storage — SOPS + age.** Encrypted `secrets.enc.yaml` committed to the repo, decrypted only at deploy time on the VPS (age key on the VPS + the operator's Mac, never in git). Secrets get version history, rotation becomes a visible commit, there is no SaaS dependency and no runtime dependency that can take a deploy down. **Back the age private key up in a password manager, not only on two machines that can both fail** — losing it loses everything.
- **Runtime — compose secrets, not env vars.** Env vars leak into `docker inspect`, crash dumps, child processes, and any library that dumps `environ`. `pydantic-settings` supports `secrets_dir` natively, so `config.py` takes a small change, not a rewrite. For a project whose Phase 6 is a security writeup this is the better artifact.
- **The most sensitive value here is `EDGAR_IDENTITY`**, not the API keys — it is personal data, and the repo history has been scrubbed of it once already.
- **Which secret exists in which context** — measured against `config.py`, where **exactly one role is on Anthropic** (`eval_judge`, `claude-sonnet-5`) and every other role is OpenRouter:

  | | `fie-api` (serving) | VPS eval cron | GitHub Actions |
  |---|---|---|---|
  | `OPENROUTER_API_KEY` | ✓ | ✓ | — |
  | `OPENAI_API_KEY` (query-time embeddings) | ✓ | ✓ | — |
  | `LANGFUSE_*` (Cloud) | ✓ | ✓ | — |
  | `ANTHROPIC_API_KEY` | **✗** | ✓ (judge only) | — |
  | `EDGAR_IDENTITY` | **✗** | **✗** | — |

  Two things this table is load-bearing about. `EDGAR_IDENTITY` is needed only to hit SEC during **ingest**, so the serving path and the eval both run without the most sensitive value present. And the serving container never holds the judge key — which matters because `eval_judge` is the measuring stick, not a product dependency.
- **Do not move `eval_judge` off Anthropic to drop a key.** It saves nothing on the serving path (see the table), ADR 0014 makes an Anthropic model via OpenRouter raise by design, and CLAUDE.md records the judge flipping on ~1 in 27 byte-identical spans — a different judge shifts the whole scored population, so `eval/baselines.json` would stop being comparable and the 2026-08-01 baseline would have to be re-earned.
- **GitHub Actions holds no secrets at all** (see deliverable 5): tests are offline, and image publishing uses the per-job `GITHUB_TOKEN`. Never bake a secret into an image.
- For the "no secret in history / image layer / CI log" gate item, name the tools: `gitleaks` or `trufflehog` over full history in CI, `docker history --no-trunc` on the built image. The CI-log third of that item is now satisfied by construction — no workflow receives a secret — but keep the scanners: they catch a secret *committed*, which is a different failure. Record why GH Actions masking is not a control anyway: it **fails open on transformed values** (a base64'd secret is not masked).
- ADR with alternatives: plain `.env` on the VPS (rejected: no history, no recovery if the box dies, demonstrates nothing); Infisical/Vault/Doppler (rejected: overkill for one VPS and ~6 secrets, and each adds a runtime dependency that can break a deploy).

### 5. CI/CD (GitHub Actions)

**No live eval on PRs. The eval-smoke-subset gate the blueprint implies was specced and rejected here — the subset is the problem, not the frequency.** Three measured reasons, all checked against the code on 2026-08-03:

- `--limit N` takes `questions[:N]`, and `expected_fact_coverage` iterates the list it is handed. The first 8 gold questions carry **8 of the 28** XBRL labels, so a subset run's coverage is measured over a *different population* than the `1.000` in `baselines.json`. Comparing them is the same category error CLAUDE.md already records for `citation_accuracy` — the population moved, not the per-class rates.
- The first 8 contain **0 of the 4 unanswerable questions** — the ones that test whether the system declines instead of confabulating when the corpus has no answer. A gate blind to that regression class is blind to the project's entire reason for existing.
- The denominators are too small to gate on. The accepted baseline scored 217 rows over 30 questions, so ~58 over 8: one unsupported claim is 0.017 against a 0.02 target, two fails the build. It would flip on noise, get re-run reflexively, and a gate you re-run reflexively is worse than none — it manufactures false assurance.

Note also that `on: pull_request` fires on `synchronize`, i.e. **every push to a branch with an open PR** — not only at merge. Any live-key job on that trigger runs far more often than it looks.

**So: run the full set, rarely, at the moments the answer matters.** At $0.023/question a full 30-question run is **$0.69** (agent spend from `run_budget` only — this **excludes the `eval_judge` calls**, so treat it as a floor).

**And the eval does not run in GitHub Actions at all — it runs on the VPS.** Two reasons, one practical and one about blast radius:

- The eval needs the corpus, and `data/` is gitignored. A CI run would need a Qdrant service container plus `data/ingest.db` (13 MB, the XBRL facts store) restored from somewhere, plus an embed pass from `data/chunks` (12 MB) to repopulate vectors — real setup for a job that runs twelve times a year. The VPS already has all of it, because the service needs it to answer anything.
- Moving it off CI means **GitHub Actions never receives a secret**, which satisfies the "no secret in CI logs" exit item by construction rather than by discipline.

| where | trigger | what runs | secrets |
|---|---|---|---|
| GitHub Actions | every push / PR | `ruff` → `mypy` → `pytest` — free, deterministic, offline | **none** |
| GitHub Actions | push to `main` | build image → push to GHCR | **none** — `GITHUB_TOKEN` is minted per job |
| VPS `cron` | monthly | **full** `eval-agent`, report to disk; failure alerts to the same channel as uptime | on the box |
| VPS, manual | before a deploy | **full** `eval-agent`, gated against `eval/baselines.json` | on the box |

The monthly run is what a PR gate structurally cannot be: with nothing in the repo changed, it measures **external drift** — OpenRouter silently re-routing `z-ai/glm-5.2` to another provider or quantization, a `-lite` alias updated underneath us, a model deprecated, the embedding endpoint shifting. Monthly rather than weekly because it watches for drift we cannot cause.

**Three consequences of running it on the box, to design for rather than discover:**
- **The `eval_judge` key now lives on the VPS**, which is the one thing that puts an Anthropic key there. Scope it: the `fie-api` service mounts only the serving secrets, and the eval cron reads its own set. Same box, but the public-facing container cannot read the judge key.
- **Disk.** `data/checkpoints.db` is already **256 MB**, and a monthly 30-question run appends to it on a box with 11 GB free. Decide a retention rule and a periodic `VACUUM` now, not when the disk fills. This is also the file `fie export-runs` reads, so do not prune it blindly.
- **Contention.** The run takes ~75 minutes and shares 2 vCPU with five production services. It is I/O-bound on model APIs rather than CPU-bound, but schedule it at a quiet hour and run it `nice`d. Its spend is **not** governed by the public `$5/month` counter — that governs the HTTP path only, and the eval calls `answer_question()` directly — so size the provider balance to cover $0.69/month of eval *plus* the demo.

**Deploy is manual and CI holds no credential for it.** CI builds the image and pushes it to GHCR; the operator runs `ssh vps` → `docker compose pull && docker compose up -d` → smoke test (`healthz` + one canned question). **The rejected alternative is the Tailscale GitHub Action** (ephemeral node → `ssh` → deploy), and the reason is worth an ADR line: API keys in CI are bounded by the provider-side caps and recoverable by rotation, but a tailnet credential grants *network reachability* to the operator's Mac and to a box running five production services. Those are different failure classes and only one is worth accepting. Building in CI still earns its keep — it keeps a `uv` build off a 2-vCPU box where stale build cache had already reached 2.2 GB.

`uv run eval-agent --limit N --skip-judge` stays what CLAUDE.md already calls it — a **local** iteration tool, never a gate.

Considered and rejected: recorded-cassette replay of real model responses to give a behavioral check on every push for $0. It catches only plumbing regressions (the responses are frozen by construction, so quality drift is invisible), and `StructuredFakeModel` in `tests/fakes.py` already covers most of that ground. Real work, narrow gain.
- The repo needs to be on GitHub by this phase (create it in Phase 0 in practice; CI config lands here).

### 6. Monitoring (blueprint §7: health, error rates, cost per run, latency)

- Langfuse **Cloud** dashboards for cost/latency/error-rate per run and per node (data's already there — this is configuration + a `docs/` note on what to watch).
- Uptime: a simple external ping on `healthz` at `https://api.example.com`, alerting to email/ntfy. **Use a free external service, not an Uptime-Kuma container** — that is another ~150 MB on a box with 1.8 GB available, spent to monitor a service the same box is running. An external prober is also the only one that can tell you the box is down.
- **Spend monitoring**: expose the governor's day/month totals on an admin endpoint and alert before the cap, not on it. The provider-side hard caps (deliverable 1) are the backstop, but hitting them silently means the demo is down.
- **Load monitoring**, which matters more without a CDN in front: alert on `limiting requests, excess:` appearing in the fie nginx error log (it is also the fail2ban trigger) and on fail2ban ban events. A quiet log means the limits are sized right; a noisy one is the upgrade trigger firing.
- Log rotation configured for containers **and for the new nginx vhost logs before deploying** — see the disk note in deliverable 2. Public traffic writes access logs that the five existing services' rotation config does not cover.

## Tests

- API unit tests (`httpx` test client, graph mocked): job lifecycle, HITL review round-trip, resume-after-interrupt, authz, input validation (question length caps, ticker allow-list — first line of the Phase 6 sanitization story).
- **Abuse-control tests, all offline** — these guard real money, so they get the same rigor as the graph invariants: the spend governor returns `429` once the day/month cap is crossed and *stays* closed; per-IP quota counts per IP and resets on the day boundary; a submission without a valid Turnstile token is refused **even with a correct `X-API-Key`** (this is the assertion that encodes "the SPA key is not a control"); the semaphore admits at most N concurrent runs and the queue-depth cap returns `429` rather than queueing without bound; the cache returns a hit only on an exact normalized match and never across differing company scope.
- Recorded-run export: the emitted JSON validates against the same Pydantic response model the live endpoint returns, so Phase 5 renders both from one contract.
- **The nginx and fail2ban limits are not pytest-testable** — they live above the app. They get a scripted check run against the deployed host instead: a burst of `POST /v1/questions` returns `429` from *nginx* (distinguishable from the app's `429` by response body), a burst of polls does not, and repeated `429`s produce a fail2ban ban that expires. Keep it as a script in `scripts/`, not a CI job — it needs the live host.
- One live end-to-end against the deployed VPS instance (manual gate in CI).

## Exit gate

- `curl` from the operator's Mac (tailnet) and from `https://api.example.com` off the tailnet: submit a question → poll → retrieve a verified answer with grounding table; a run with unresolved claims stops at `awaiting_review` and completes after a review action.
- CI green on the free checks (`ruff`/`mypy`/`pytest`) for every push, and CI publishes the image to GHCR. **One full `eval-agent` run, on the VPS, passing its gate against `eval/baselines.json` before the deploy** — that is the "agents still reliable" evidence, not a PR-time subset. The monthly cron is installed and has produced at least one report. Manual `docker compose pull && up -d` deploys end-to-end; **Langfuse Cloud** shows the production traces.
- No secret exists in the repo history, in an image layer, or in CI logs — the third is by construction (no workflow receives one), so **verify it rather than assume it**: grep the workflow files for `secrets.` and confirm the only match is `GITHUB_TOKEN`.
- **The box is still healthy with `fie` on it**: `free -h` shows headroom, no OOM events in `dmesg`, and **all five pre-existing projects still up** — that last one is a hard gate, not a courtesy.
- **The load controls are proven under a burst, not only configured.** Drive a burst at the public host and confirm three things at once: nginx returns `429` for submissions, **`rec.neighbour-c.example` and the other four keep serving normally throughout** (this is the whole point of `limit_conn $server_name`), and the box's CPU and memory stay inside the container limits. Without a CDN in front, this is the gate that replaces one.
- **The spend governor is proven against the real service, not only in tests**: drive the day cap down to a few cents, submit until it trips, confirm `429` and that the provider-side caps were never approached.
- ADRs: job model (confirming 3.1 as implemented), **Langfuse hosting (the measured reversal)**, **public exposure via the existing nginx on `sslip.io` (the three accepted costs, and Funnel/domain as the upgrade trigger)**, SOPS secrets + compose-secrets injection, **public-demo abuse controls (load vs. spend, why no Cloudflare Worker, why the load layer is nginx-side)**, **Qdrant instance isolation**, **eval cadence and location (why the smoke subset is not a gate, why monthly drift detection, and why it runs on the VPS rather than in CI)**, **manual deploy (why CI gets no tailnet credential: capped-and-rotatable spend vs. uncapped network reach)**, HITL gate semantics.
