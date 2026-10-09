"""The REST embedding client's failure behaviour — it is on the answer path."""

from collections.abc import Callable

import httpx
import pytest

from fie.ingest import embed
from fie.ingest.embed import OpenAIEmbedder

OK = {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}


def embedder_over(
    handler: Callable[[httpx.Request], httpx.Response], monkeypatch: pytest.MonkeyPatch
) -> OpenAIEmbedder:
    monkeypatch.setattr(embed.time, "sleep", lambda _s: None)
    client = OpenAIEmbedder("test-key", dim=2)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_transient_status_is_retried(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status) if len(calls) == 1 else httpx.Response(200, json=OK)

    assert embedder_over(handler, monkeypatch).embed(["q"]) == [[0.1, 0.2]]
    assert len(calls) == 2


def test_a_dropped_connection_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=OK)

    assert embedder_over(handler, monkeypatch).embed(["q"]) == [[0.1, 0.2]]
    assert len(calls) == 2


def test_a_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 or 400 will not get better on the next attempt."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)

    with pytest.raises(httpx.HTTPStatusError):
        embedder_over(handler, monkeypatch).embed(["q"])
    assert len(calls) == 1


def test_a_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """At 120s a second hang outlives the public node timeout, so retrying
    one only delays the abort."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(httpx.ReadTimeout):
        embedder_over(handler, monkeypatch).embed(["q"])
    assert len(calls) == 1


def test_a_failure_that_persists_raises_after_the_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        embedder_over(handler, monkeypatch).embed(["q"])
    assert len(calls) == embed._MAX_ATTEMPTS
