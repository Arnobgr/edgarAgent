"""Tracing is optional: an unreachable Langfuse must never fail a run. No network."""

import logging

import httpx
import pytest
from langfuse import Langfuse
from pydantic import SecretStr

from fie.config import Settings
from fie.observability import get_langfuse_handler


def test_an_unreachable_langfuse_costs_the_trace_link_not_the_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`get_trace_url` is the one synchronous Langfuse call in a run. On
    2026-10-17 it raised `ConnectError` with the local stack stopped and failed
    `fie ask` before the graph started; the server makes the same call to
    Langfuse Cloud for every public question."""

    def refuse(self: Langfuse, *, trace_id: str) -> str:
        raise httpx.ConnectError("[Errno 61] Connection refused")

    monkeypatch.setattr(Langfuse, "get_trace_url", refuse)
    settings = Settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key=SecretStr("sk-lf-test"),
        langfuse_host="http://127.0.0.1:9",
    )
    with caplog.at_level(logging.WARNING, logger="fie.observability"):
        handle = get_langfuse_handler("0123456789ab", settings)

    assert handle is not None
    assert handle.handler is not None
    assert handle.trace_url is None
    assert "Langfuse unreachable" in caplog.text
