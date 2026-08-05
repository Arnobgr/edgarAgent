# ADR 0026 — The job model: in-process asyncio, bounded, polled

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Confirms:** `plans/00-overview.md` §3.1 (no broker, no worker fleet) ·
**Extends:** ADR 0019 (one connection, one lock)

## Context

A run takes ~75 seconds and can reach ten minutes. HTTP cannot hold that, so
the API needs a job model, and the obvious reflex is Celery or RQ with Redis —
a broker, a worker fleet, a second process to deploy and monitor.

`00-overview.md` §3.1 banned that in advance. This ADR is that decision as
code, and records what the implementation had to settle that the plan did not.

The constraint that decides most of it is the box: **2 vCPU, ~1.8 GB
available**, already running five production services (`plans/phase-4`
inventory, measured 2026-08-03). A broker plus a worker is a third and fourth
container on a host where `fie-api` is capped at 640 MB precisely because
there is no room to be casual.

## Decision

**One process. `asyncio.create_task` per job, a SQLite row per job, and the
caller polls.** The graph is already async end to end, so a job is a coroutine
and the "queue" is the event loop.

**Two bounds, guarding different things.** They are separate on purpose and
conflating them is how this goes wrong:

| | value | what it guards |
|---|---|---|
| `api_max_concurrent_runs` semaphore | 2 | **memory** — at most two graphs hold evidence, claims and an LLM client at once, sized against `mem_limit: 640m` |
| `api_max_queued_runs` cap | 4 | **honesty** — past it, submit returns `429` rather than accepting a job nobody will reach for twenty minutes |

A `202` for work that will not start inside the caller's patience is a failure
wearing a success code. The queue cap is what stops the API from issuing one.

**Serving state gets its own SQLite file** (`data/api.db`): jobs, the spend
ledger, the per-IP quota, the answer cache. Separate from `ingest.db`
(read-mostly corpus truth) and from `checkpoints.db` (LangGraph's, and what
`fie export-runs` reads), so serving state can be pruned, backed up or dropped
without putting either of the other two at risk. The connection is shared
across the event loop and the background tasks **under one lock** — the same
arrangement `IngestStore` uses, for the reason ADR 0019 paid for: a
close/query race on a shared SQLite connection killed a 75-minute eval run.

**One uvicorn worker, and that is a constraint rather than a tuning knob.**
The registry is in-process, so a second worker would hold a second semaphore
and the concurrency cap — the OOM control — would admit twice what the memory
limit was sized for. The Dockerfile says so at the `CMD`.

**Progress comes from the checkpoint, not from the job row.** The row is
written when the graph returns; for a 75-second run that is a long time to
show the caller nothing. `progress.py` reads the same checkpoint file
`answer_question` writes, through a second connection held for the app's
lifetime.

**Startup recovery instead of a heartbeat.** On boot, jobs left `running` by a
dead process are marked `interrupted` and resumable through
`POST /v1/jobs/{id}/resume`, which replays from the LangGraph checkpoint. This
is decision 3.4 paying off: durability came from checkpointing, so it costs no
new machinery.

## Consequences

The whole surface — including the abuse controls that guard real money — runs
against a fake engine in `make test`, with no network, no keys and no corpus,
because `create_app` injects its dependencies the way `build_graph` does.
46 of the 242 tests exercise the HTTP layer and the review gate, and none of
them need a broker running.

A restart drops in-flight jobs. That is survivable *because* of checkpointing
and resume, and it is the honest trade for not operating a broker.

**Upgrade trigger** (from §3.1, restated so it is not re-derived): sustained
demand past what 2 concurrent runs absorb, or a need to survive restarts
without a manual resume. Then a broker earns its keep. Not before.

## Honest limits

- The queue cap of 4 is reasoned from the memory table, not measured under
  load. `scripts/burst-test.sh` measures it against the deployed host; until
  that runs, treat both numbers as sized rather than validated.
- Polling costs the frontend a request every second or two. That is why the
  nginx zones split submit from poll (ADR 0035) — a single rate limit would
  have to be loose enough for the poll path, which makes it useless for
  submit.
- `interrupted` is set at startup, so a job orphaned by a crash stays
  `running` in the database until the next boot. Nothing reads that window,
  but a status shown to a user during it would be wrong.
