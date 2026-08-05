# ADR 0034 — The review gate: what a human is allowed to change

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted ·
**Extends:** ADR 0021, ADR 0023 (retraction integrity)

## Context

The blueprint's topology has a human-in-the-loop gate: runs with claims the
machine could not resolve stop before finalization and a person rules on them.
It lands in Phase 4 because it needs an API surface to exist — a CLI prompt is
not a review workflow.

The risk is specific and it is not "a human might be wrong". It is that a
human verdict, stored carelessly, becomes indistinguishable from a machine
verdict, and then `grounding_rate` measures a mixture of what the system
proved and what somebody waved through.

## Decision

**`accepted` is its own status. An accepted claim never becomes `grounded`.**
That single choice is what keeps the gate from laundering a human opinion into
a gate metric. `grounding_rate` counts what the reconciler and the judges
proved; nothing a reviewer does can move it up.

**An explicitly retracted claim is normalised to `unsupported`**, which is the
status `retract` excises — so the reviewer's decision flows through the
existing, tested excision path rather than a second one.

**A claim with no decision keeps the status the machine gave it.** The gate
only ever changes an outcome a human explicitly changed. A reviewer who closes
the tab changes nothing.

**Three modes** (`api_review_mode`): `off`, `unresolved`, `always`. **Off by
default and off in the eval**, so the gate can never move a gate metric —
stated in `build.py`'s module docstring, not only here.

**The `review` node is deliberately not wrapped in `guarded`.** Every other
node is bounded by `node_timeout_seconds` because it holds an LLM call; this
one holds a *person*. `interrupt` raises immediately and the waiting happens
outside the graph, so the timeout could never fire — but wrapping it would
claim otherwise, and the budget stamp it adds is noise for a node that spends
nothing.

**Decisions travel as a list, and the shape is part of the contract.**
LangGraph reads a `dict` resume value as a map of interrupt ids when
`all(is_xxh3_128_hexdigest(k) for k in resume)` — and `all({})` is `True`, so
`Command(resume={})` re-raises the interrupt instead of resuming it. A list
cannot be misread that way. `ReviewDecision`'s docstring carries the reason so
nobody "tidies" it back into a mapping.

**Over HTTP:** `GET /v1/jobs/{id}/review` lists only unresolved claims;
`POST` accepts decisions and resumes from the checkpoint. An unknown claim id
is a `422`, and reviewing a job that is not waiting is a `409` — a review that
silently applies to nothing is worse than a refusal.

## Consequences

Six tests cover the round trip: the queue listing only unresolved claims, the
unknown-id rejection, the `202`, and — the one that matters —
`engine.calls[1]["question"] is None` with the decisions carried, proving the
resume replays from the checkpoint rather than starting a second run.

`RunResult.awaiting_review` is part of the output contract, so a paused run is
visible to the CLI and the export alike. `fie export-runs` uses the same fact
from the other side: a thread parked at the gate stores its interrupt as a
pending write, which is how an unfinished run is detected without
reconstructing the graph (ADR 0027).

## Honest limits

- **The gate is `off` on the public path** (`docker-compose.prod.yml`, decided
  2026-08-05). It shipped as `unresolved`, and two limits below decided
  against it. The feature stays exercised by tests and reachable over the
  tailnet; it is simply not offered to strangers.
- There is no reviewer identity. A decision is anonymous, so the record shows
  that a human ruled, not which one. Fine for one operator; not fine for
  anything audited — and on a page with no login, "a human" means any visitor.
- A job can wait at the gate indefinitely, holding a semaphore slot. Nothing
  expires it, and with `api_max_concurrent_runs: 2` two abandoned reviews
  would close the public path until a restart. **This is the concrete reason
  the public path runs `off` rather than a hypothetical one**: the failure
  needs no bad actor, only two people who close a tab.
