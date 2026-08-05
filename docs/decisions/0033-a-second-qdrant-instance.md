# ADR 0033 — A second Qdrant instance, not a shared one

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted

## Context

The VPS already runs a Qdrant for `neighbour-c`. This project's collections are
already namespaced (`fie_chunks__*`, one per embedding model — ADR 0011), so
reuse would work today and would save roughly **150 MB** of process baseline
on a box with 1.8 GB available. On a host this tight, 150 MB is not nothing.

Two facts push the other way. The existing instance is at **67% of its memory
limit**. And this project runs `fie ingest`, which writes vectors in bulk.

## Decision

**Run `fie-qdrant` as a second instance under the `fie` compose project**,
capped at 384 MB / 0.5 CPU, unpublished (reachable only on the compose
network).

The corpus is 4,181 points × 1536 dims ≈ **26 MB of vectors**, so 384 MB is
generous — the isolation costs a baseline, not a working set.

**The reason is blast radius, not namespacing.** Namespacing prevents a
*collection* collision. It does not prevent an ingest run in a demo project
from pushing a shared process — already at 67% — into its memory limit, and
that process serves a production service. A demo must not be able to do that.

150 MB is a fair price for that, and it matches the "isolate under its own
compose project" rule the other five services on this box already follow.

## Consequences

`fie-qdrant` is on its own named volume (`fie_qdrant_storage`), backed up
nightly by `deploy/backup.sh`, and can be dropped and re-ingested without
touching anything else on the box.

Two Qdrant instances now run on 2 vCPU. Both are capped, so the CPU
contention is bounded by `cpus: 0.5` rather than by hope.

## Honest limits

- 384 MB is sized from the vector count, not from measured RSS under query
  load with the HNSW index warm. `docker stats` during `scripts/burst-test.sh`
  is the check.
- If the corpus grows past TSLA/AAPL/NVDA, this number is the first one to
  revisit — and on this box there is no headroom to absorb the growth
  silently.
