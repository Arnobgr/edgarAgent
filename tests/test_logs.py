"""Structured logging: what reaches the container's JSON log stream."""

import json
import logging

import pytest
import structlog

from fie.api.logs import configure_logging


def test_extra_fields_and_bound_context_render_as_json_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The node-transition event is only useful as data: without
    `ExtraAdder` its `extra={...}` fields would be dropped by the formatter
    and the line would carry nothing a query could filter on."""
    configure_logging(json_logs=True)
    structlog.contextvars.bind_contextvars(job_id="abc123")
    try:
        logging.getLogger("fie.test").info(
            "node verify: ok in 0.1s", extra={"node": "verify", "duration_ms": 100}
        )
    finally:
        structlog.contextvars.clear_contextvars()

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["node"] == "verify"
    assert line["duration_ms"] == 100
    assert line["job_id"] == "abc123"
