# ADR 0028 — One correlation id across the log line, the trace and the job

**Date:** 2026-08-05 · **Phase:** 4 · **Status:** accepted

## Context

Phase 3 debugging worked because a failed run could be reconstructed from its
checkpoint. A service adds two more records of the same event — an HTTP
request and a Langfuse trace — and the failure mode is well known: three
systems that each know part of what happened and share no key, leaving
timestamps as the only join.

There is also a format problem. The engine logs through stdlib `logging`
everywhere (`logger.info(...)` inside the graph nodes). Asking forty call
sites to import structlog to get JSON in production would be a large change
for a formatting concern.

## Decision

**Install the structlog processor chain on the stdlib root logger.** The rest
of the codebase keeps calling `logging`, and `ProcessorFormatter` renders it —
JSON in the container (`API_LOG_JSON=true`), key-value on a terminal.

**Bind the ids as context variables, not as arguments.** A request binds
`request_id`; a job binds `job_id` and the Langfuse `trace_id`. Every line
emitted inside that scope carries them, including lines from graph nodes that
know nothing about HTTP.

**`X-Request-ID` is echoed if the caller sent one and minted if not**, so a
request id survives a proxy hop and a caller can correlate from their side.

**The trace id is derived deterministically from the job id** rather than read
back from Langfuse. Tracing is not on the answer path (ADR 0032), and an id
that only exists if an external SaaS responded is not a correlation key — it
is a second dependency.

**Re-point uvicorn's loggers at the root.** Found by running the built image,
not by reading the code: uvicorn installs its own handlers on `uvicorn`,
`uvicorn.error` and `uvicorn.access` and sets `propagate = False`, so its
access lines print as plain text beside everything else's JSON. Half a JSON
log stream is worse than none — a parser chokes on it. `configure_logging`
clears those handlers and hands the loggers back.

## Consequences

A log line, a Langfuse trace and a row in `api.db` can be lined up after the
fact from any one of them. Verified in the built image: all eight startup and
request lines render as JSON, including uvicorn's.

## Honest limits

- Deriving the trace id means it is correct only while `observability/` keeps
  deriving it the same way. That is one function, and the API test asserts the
  value, but it is a convention rather than a handshake.
- `contextvars` propagate into tasks created inside the bound scope. A task
  created outside it — none exist today — would log without ids and nothing
  would flag it.
