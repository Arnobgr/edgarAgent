"""Observability: Langfuse wiring and trace helpers.

This is the only module that imports Langfuse. Everything else attaches to
tracing through the handle returned by `get_langfuse_handler()` and the
generic LangChain callback/metadata mechanisms.

Blueprint rule: every node, tool call, token count, and cost must appear in
the trace.
"""

import logging
from dataclasses import dataclass
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from fie.config import PHASE, Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceHandle:
    """A configured Langfuse callback handler plus the trace it reports to."""

    handler: BaseCallbackHandler
    trace_id: str
    trace_url: str | None
    _client: Langfuse

    def flush(self) -> None:
        """Block until buffered events are delivered (call before CLI exit)."""
        self._client.flush()


def new_run_id() -> str:
    """Twelve hex characters: the run id, thread id and job id are one value."""
    return uuid4().hex[:12]


def trace_id_for(run_id: str) -> str:
    """The Langfuse trace id a run will report under.

    Derived from the run id by the same seeded construction
    `get_langfuse_handler` uses, so the API can hand a caller the trace link
    for a job without holding a Langfuse client or waiting for the run to
    produce one."""
    return str(Langfuse.create_trace_id(seed=run_id))


def trace_metadata(run_id: str, phase: str = PHASE) -> dict[str, object]:
    """Metadata to pass in the graph's invoke config: tags the whole trace
    with the run id and phase (and shows up on every span)."""
    return {
        "run_id": run_id,
        "phase": phase,
        "langfuse_tags": [f"run:{run_id}", phase],
    }


def get_langfuse_handler(run_id: str, settings: Settings | None = None) -> TraceHandle | None:
    """Build the Langfuse LangChain callback handler for one run.

    Returns None (with a warning) when Langfuse keys are not configured, so
    the engine still runs before the local Langfuse stack is set up.
    """
    s = settings or get_settings()
    secret = s.langfuse_secret_key.get_secret_value()
    if not s.langfuse_public_key or not secret:
        logger.warning(
            "Langfuse keys not configured (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY): "
            "running without tracing. Start the stack with "
            "`docker compose -f docker-compose.dev.yml up -d` and copy keys into .env."
        )
        return None

    client = Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=secret,
        host=s.langfuse_host,
    )
    trace_id = Langfuse.create_trace_id(seed=run_id)
    handler = CallbackHandler(
        public_key=s.langfuse_public_key,
        trace_context={"trace_id": trace_id},
    )
    return TraceHandle(
        handler=handler,
        trace_id=trace_id,
        trace_url=client.get_trace_url(trace_id=trace_id),
        _client=client,
    )
