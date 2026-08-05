"""Structured logging for the service.

JSON in the container (where a log line is data for `docker logs` and
whatever reads it), key-value on a terminal. Every line inside a request
carries its `request_id`, and every line inside a job carries the `job_id`
and the Langfuse `trace_id` — so a log line, a trace and a job row can be
lined up after the fact without guessing from timestamps.
"""

import logging
from typing import Any

import structlog


def configure_logging(*, json_logs: bool, level: int = logging.INFO) -> None:
    """Route stdlib logging through structlog.

    The engine logs through stdlib `logging` everywhere (`logger.info(...)`
    in the graph nodes), so the processor chain is installed on the stdlib
    root rather than asking the rest of the codebase to import structlog.
    """
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processor=renderer)
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs handlers of its own on these and sets
    # `propagate = False`, so without this its access lines print as plain
    # text beside everything else's JSON — half a log stream that a parser
    # chokes on. Hand them back to the root logger.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
