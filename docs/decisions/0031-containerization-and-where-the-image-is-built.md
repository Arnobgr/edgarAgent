# ADR 0031 — Containerization, and why the image is built in CI

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Depends on:** ADR 0032 (the box only fits because Langfuse is not on it)

## Context

The target is a 2 vCPU box with ~1.8 GB available and 11 GB free disk, already
running five production services. Stale Docker build cache had reached 2.2 GB
on it. `.venv` for this project is 597 MB and pulls `onnxruntime` through
`fastembed`, which is needed **at query time** for the sparse BM25 vector — it
is not an ingest-only dependency that could be trimmed out of the runtime.

## Decision

**Multi-stage build, `uv sync --locked`, non-root runtime.** Standard, and the
standard parts are not what this ADR is for. Three things are not boilerplate:

**`libgomp1` in the runtime stage.** `python:3.12-slim-bookworm` does not
carry it and `onnxruntime` fails at *import* without it. Found by running the
image, not by reading the Dockerfile.

**The BM25 model is downloaded at build time**, into `FASTEMBED_CACHE_PATH`.
Otherwise `fastembed` fetches `Qdrant/bm25` from HuggingFace on the first
query — putting a third-party download on the answer path, and on the *first
user's* answer path specifically.

**A fixed uid (10001).** The named volume takes its ownership from the image,
so a uid that shifts between builds makes `/app/data` unwritable after an
upgrade.

**One uvicorn worker**, restated here because it looks like a tuning
oversight: the job registry is in-process asyncio (ADR 0026), so a second
worker holds a second semaphore and the concurrency cap — the OOM control —
would admit twice what the memory limit was sized for.

**Every container gets an explicit `mem_limit` *and* a CPU limit**, because
there are only 2 vCPU and memory limits alone would let a runaway starve five
production services of CPU:

| | `mem_limit` | `cpus` | why |
|---|---|---|---|
| `fie-api` | 640 MB | 1.0 | the 597 MB `.venv` and `onnxruntime`; one core leaves one for everyone else |
| `fie-qdrant` | 384 MB | 0.5 | 4,181 points × 1536 dims ≈ 26 MB of vectors — generous |

Plus `pids_limit`, `no-new-privileges`, `cap_drop: ALL`. ~1.0 GB against
1.8 GB available, with 2 GB of untouched swap as backstop.

**`fie-api` binds to `127.0.0.1` only.** nginx reaches it over loopback; the
port is never on a public interface.

**The image is built in GitHub Actions and pulled by the VPS.** This is the
one thing CI does that the box could also do, and it earns its keep: `uv sync`
plus a model download on 2 vCPU, on a host where build cache had already
reached 2.2 GB against 11 GB free. Building elsewhere keeps the builder off
the box entirely.

**The image holds no secret and can hold none by construction**: no build
args, no mounted secrets, and `.dockerignore` excludes `.env`, `secrets/`,
`data/` and the local-only blueprint from the build context.

## Consequences

1.07 GB image, verified end to end before anything shipped: it refuses to boot
without `API_KEY`; `/healthz` reports `{"status":"degraded","qdrant":false}`
with an unreachable Qdrant; 401 without a key, 202 with one; and the run then
fails cleanly with `RuntimeError: Qdrant is unreachable`. Running it is also
what surfaced the uvicorn logging defect (ADR 0028) and `libgomp1`.

`ANTHROPIC_API_KEY` and `EDGAR_IDENTITY` are deliberately absent from the
compose service (ADR 0029).

## Honest limits

- 640 MB is derived from the `.venv` size and the RAM inventory, not from
  observed RSS under two concurrent runs. `docker stats` during
  `scripts/burst-test.sh` is the check, and it has not run yet.
- The image is large for what it does; `onnxruntime` is most of it. Trimming
  it means changing the retrieval stack, which is decided by measurement
  (ADR 0011), not by image size.
- A `latest` tag is what the deploy pulls. It is also tagged by commit sha, so
  a rollback is possible, but the default path is not pinned.
