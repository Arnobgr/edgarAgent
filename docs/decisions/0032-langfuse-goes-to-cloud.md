# ADR 0032 — Langfuse goes to Cloud: a measured reversal

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Reverses:** the Phase 4 plan's original "migrate the self-host stack to the
VPS"

## Context

The plan said to move the local Langfuse self-host stack onto the VPS with
everything else. Then the box was measured (2026-08-03).

Langfuse self-hosted is **six containers**: Postgres, ClickHouse, MinIO,
Redis, web, worker. ClickHouse alone wants 1–2 GB. Available on the box:

| | |
|---|---|
| neighbour-c recommender | 342 MB (capped 600 M) |
| neighbour-c postgres | 43 MB (capped 250 M) |
| four uncapped API containers | ~400 MB |
| host (dockerd, nginx, tailscaled, …) | ~400 MB |
| **available** | **1.8 GB**, ≈700 MB if the capped containers reach their limits |

It does not fit. And it does not fail gracefully: this box has no memory
headroom, so the failure mode is the OOM killer choosing among **five running
production services**.

## Decision

**Production traces go to Langfuse Cloud's free tier.**
`docker-compose.dev.yml` stays exactly as it is for local development. No
trace export or import from dev — a fresh start is fine, because the dev
traces were debugging material, not records.

Free-tier allowance checked by the operator 2026-08-03 and confirmed sufficient: at
~200 runs/month it is not close to binding.

**Langfuse is not a health dependency.** `/healthz` reports it as an
informational field and never in the pass/fail. Tracing is not on the answer
path, and letting an external SaaS turn this service red would page the operator for
someone else's outage.

That is also why the correlation trace id is *derived* from the job id rather
than read back from Langfuse (ADR 0028) — an id that exists only if a SaaS
responded is a second dependency, not a correlation key.

## Consequences

The `fie-api` + `fie-qdrant` pair fits in ~1.0 GB against 1.8 GB available
**only because Langfuse is not there** (ADR 0031). This decision is what makes
the deployment possible, not merely tidier.

Recording it as a reversal is the point. The sequence — planned, measured the
target, did not fit, decided, recorded the upgrade trigger — is the process
working, and a plan silently amended would look like an oversight instead.

**Upgrade trigger:** a bigger box, or a second cheap VPS dedicated to
Langfuse, if self-hosting becomes worth it. Neither is worth it for ~200
runs/month.

## Honest limits

- Traces now leave the machine and land with a third party. For a demo over
  public SEC filings that is acceptable; for anything with client data it
  would not be, and this ADR would have to be revisited rather than cited.
- The free tier has a retention window. Nothing depends on old traces today —
  diagnosis runs off `checkpoints.db` (see CLAUDE.md) — but "check Langfuse"
  stops being an answer past that window.
- Dev and prod now differ in one dependency. The tracing code path is the
  same; the *host* is not, so a Cloud-specific failure would not reproduce
  locally.
