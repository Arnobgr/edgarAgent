"""The HTTP surface: lifecycle, contract, the review round-trip and recovery.

The engine is faked throughout (`FakeEngine`), so these prove what the service
does with a run rather than what the graph does inside one — the graph's own
review semantics are asserted in `test_review_gate.py`. The abuse controls get
their own file because they guard real money.
"""

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from fie.api import create_app
from fie.api.app import _facts_store_ok
from fie.api.models import RunPayload
from fie.api.progress import _phase
from fie.api.store import ApiStore
from fie.config import Settings
from fie.state import GroundingRow, RunResult
from tests.conftest import API_KEY
from tests.fakes import FakeEngine, api_client, make_result, wait_for_status

AUTH = {"X-API-Key": API_KEY}


def unresolved_result() -> RunResult:
    """A run that stopped at the review gate with one claim the verifier
    could not support."""
    return make_result(
        awaiting_review=True,
        answer="Net sales were $391.0 billion [c1]. Growth came from Vision Pro [c2].",
        grounding=[
            GroundingRow(
                claim_id="c-1",
                text="Net sales were $391.0 billion",
                claim_type="numeric",
                status="grounded",
                verdict="grounded",
            ),
            GroundingRow(
                claim_id="c-2",
                text="Growth came from Vision Pro",
                claim_type="qualitative",
                status="unsupported",
                verdict="unsupported",
            ),
        ],
    )


async def test_submitting_a_question_runs_it_and_returns_the_grounding_table(
    api_settings: Settings,
) -> None:
    engine = FakeEngine()
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        accepted = await client.post(
            "/v1/questions", json={"question": "What were Apple's net sales?"}, headers=AUTH
        )
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        assert accepted.json() == {"job_id": job_id, "status": "queued", "source": "live"}

        status = await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})
        assert status["source"] == "live"
        assert status["error"] is None

        result = await client.get(f"/v1/jobs/{job_id}/result", headers=AUTH)
        assert result.status_code == 200
        payload = RunPayload.model_validate(result.json())
        assert payload.job_id == job_id
        assert payload.source == "live"
        # The engine is told which run id to use, so the checkpoint thread,
        # the job row and the Langfuse trace all line up on one id.
        assert payload.result.run_id == job_id
        assert [row.status for row in payload.result.grounding] == ["grounded"]


async def test_company_scope_reaches_the_engine_in_the_question(api_settings: Settings) -> None:
    engine = FakeEngine()
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "How did revenue move?", "companies": ["aapl"]},
            headers=AUTH,
        )
        job_id = response.json()["job_id"]
        await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})

    assert "Restrict the answer to: AAPL" in engine.calls[0]["question"]


async def test_result_is_a_conflict_until_the_run_finishes(api_settings: Settings) -> None:
    hold = asyncio.Event()
    engine = FakeEngine(hold=hold)
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        ).json()["job_id"]
        early = await client.get(f"/v1/jobs/{job_id}/result", headers=AUTH)
        assert early.status_code == 409
        hold.set()
        await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})


async def test_an_unknown_job_is_a_404(api_settings: Settings) -> None:
    async with api_client(create_app(api_settings, run_fn=FakeEngine())) as client:
        response = await client.get("/v1/jobs/nope", headers=AUTH)
        assert response.status_code == 404


async def test_a_failing_run_marks_the_job_failed(api_settings: Settings) -> None:
    engine = FakeEngine(raises=RuntimeError("model exploded"))
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        ).json()["job_id"]
        status = await wait_for_status(client, job_id, headers=AUTH, until={"failed"})
        assert status["error"] == "RuntimeError: model exploded"


# --- Authentication ---


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_the_v1_surface_refuses_a_missing_or_wrong_key(
    api_settings: Settings, headers: dict[str, str]
) -> None:
    async with api_client(create_app(api_settings, run_fn=FakeEngine())) as client:
        response = await client.post("/v1/questions", json={"question": "hi"}, headers=headers)
        assert response.status_code == 401


async def test_healthz_needs_no_key_and_never_gates_on_langfuse(api_settings: Settings) -> None:
    async with api_client(create_app(api_settings, run_fn=FakeEngine())) as client:
        response = await client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        # Qdrant is unreachable in tests, so the service reports degraded —
        # and reports Langfuse as a field that took no part in that verdict.
        assert body["qdrant"] is False
        assert body["status"] == "degraded"
        assert body["langfuse_configured"] is False


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(lambda p: None, False, id="absent"),
        pytest.param(lambda p: p.touch(), False, id="empty-file"),
        pytest.param(
            lambda p: sqlite3.connect(p).executescript("CREATE TABLE facts (id INTEGER); "),
            False,
            id="table-but-no-rows",
        ),
        pytest.param(
            lambda p: sqlite3.connect(p).executescript(
                "CREATE TABLE facts (id INTEGER); INSERT INTO facts VALUES (1);"
            ),
            True,
            id="has-facts",
        ),
    ],
)
def test_facts_store_health_is_about_rows_not_about_a_file(
    tmp_path: Path, build: Callable[[Path], object], expected: bool
) -> None:
    """The first real deploy reported `facts_store: true` over an empty store.

    SQLite creates a database on connect, so the container had made a 0-byte
    `ingest.db` before the real one arrived and `path.exists()` said yes. A
    health check that goes green over an empty store reports ready for the one
    job the system cannot do — every numeric claim would fail verification
    while the service insisted it was fine.
    """
    path = tmp_path / "ingest.db"
    build(path)
    assert _facts_store_ok(path) is expected


async def test_the_service_refuses_to_start_without_an_api_key(api_settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="API_KEY is not set"):
        create_app(api_settings.model_copy(update={"api_key": SecretStr("")}))


async def test_the_service_refuses_turnstile_required_with_no_secret(
    api_settings: Settings,
) -> None:
    with pytest.raises(RuntimeError, match="TURNSTILE_REQUIRED"):
        create_app(api_settings.model_copy(update={"turnstile_required": True}))


# --- Input constraints (layer 9; first edge of the Phase 6 sanitization story) ---


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"question": "   "}, "empty"),
        ({"question": "x" * 501}, "exceeds 500 characters"),
        ({"question": "ok", "companies": ["MSFT"]}, "outside the ingested corpus: MSFT"),
    ],
)
async def test_a_question_outside_the_constraints_is_refused_before_it_costs_anything(
    api_settings: Settings, payload: dict[str, Any], expected: str
) -> None:
    engine = FakeEngine()
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        response = await client.post("/v1/questions", json=payload, headers=AUTH)
        assert response.status_code == 422
        assert expected in response.json()["detail"]
    assert engine.calls == []


# --- The human-in-the-loop gate, over HTTP ---


async def test_a_run_with_unresolved_claims_waits_for_a_reviewer(api_settings: Settings) -> None:
    engine = FakeEngine(results=[unresolved_result(), make_result()])
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Why did it grow?"}, headers=AUTH)
        ).json()["job_id"]
        await wait_for_status(client, job_id, headers=AUTH, until={"awaiting_review"})

        queue = await client.get(f"/v1/jobs/{job_id}/review", headers=AUTH)
        assert queue.status_code == 200
        # Only the claims the machine could not settle — a grounded claim is
        # not a judgement call.
        assert [row["claim_id"] for row in queue.json()["unresolved"]] == ["c-2"]

        rejected = await client.post(
            f"/v1/jobs/{job_id}/review",
            json={"decisions": [{"claim_id": "c-1", "action": "accept"}]},
            headers=AUTH,
        )
        assert rejected.status_code == 422
        assert "not awaiting review: c-1" in rejected.json()["detail"]

        resumed = await client.post(
            f"/v1/jobs/{job_id}/review",
            json={"decisions": [{"claim_id": "c-2", "action": "retract"}]},
            headers=AUTH,
        )
        assert resumed.status_code == 202
        await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})

    # The second call resumes: no question, and the decisions travel with it.
    assert engine.calls[1]["question"] is None
    assert engine.calls[1]["review_decisions"][0].model_dump() == {
        "claim_id": "c-2",
        "action": "retract",
    }


async def test_reviewing_a_job_that_is_not_waiting_is_a_conflict(api_settings: Settings) -> None:
    engine = FakeEngine()
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        ).json()["job_id"]
        await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})
        for response in (
            await client.get(f"/v1/jobs/{job_id}/review", headers=AUTH),
            await client.post(f"/v1/jobs/{job_id}/review", json={"decisions": []}, headers=AUTH),
        ):
            assert response.status_code == 409


# --- Startup recovery and resume ---


async def test_a_job_the_last_process_died_on_is_recovered_and_resumable(
    api_settings: Settings,
) -> None:
    """Decision 3.4 paying off: the asyncio task died with the process, the
    LangGraph checkpoint did not."""
    store = ApiStore(api_settings.api_db_path)
    store.create_job(job_id="orphan-1", question="Q", companies=[], client_ip="10.0.0.1")
    store.set_status("orphan-1", "running")
    store.close()

    engine = FakeEngine()
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        recovered = await client.get("/v1/jobs/orphan-1", headers=AUTH)
        assert recovered.json()["status"] == "interrupted"

        resumed = await client.post("/v1/jobs/orphan-1/resume", headers=AUTH)
        assert resumed.status_code == 202
        await wait_for_status(client, "orphan-1", headers=AUTH, until={"succeeded"})

        # Resuming a job that is not interrupted is a conflict, not a re-run.
        again = await client.post("/v1/jobs/orphan-1/resume", headers=AUTH)
        assert again.status_code == 409

    assert engine.calls[0]["question"] is None


async def test_the_trace_id_is_derivable_from_the_job_id(api_settings: Settings) -> None:
    """A log line, a job row and a Langfuse trace line up without guessing
    from timestamps — the trace id is seeded from the run id."""
    settings = api_settings.model_copy(update={"langfuse_public_key": "pk-lf-test"})
    async with api_client(create_app(settings, run_fn=FakeEngine())) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        ).json()["job_id"]
        first = await client.get(f"/v1/jobs/{job_id}/trace", headers=AUTH)
        second = await client.get(f"/v1/jobs/{job_id}/trace", headers=AUTH)
        assert first.json()["trace_id"] is not None
        assert first.json()["trace_id"] == second.json()["trace_id"]


async def test_every_response_carries_a_request_id(api_settings: Settings) -> None:
    async with api_client(create_app(api_settings, run_fn=FakeEngine())) as client:
        echoed = await client.get("/healthz", headers={"X-Request-ID": "given-id"})
        assert echoed.headers["X-Request-ID"] == "given-id"
        minted = await client.get("/healthz")
        assert minted.headers["X-Request-ID"]


async def test_cors_allows_exactly_the_configured_origin(api_settings: Settings) -> None:
    """`*` would hand the same access to any page on the internet."""
    settings = api_settings.model_copy(update={"api_cors_origins": ["https://fie.pages.dev"]})
    async with api_client(create_app(settings, run_fn=FakeEngine())) as client:
        allowed = await client.get("/healthz", headers={"Origin": "https://fie.pages.dev"})
        assert allowed.headers["access-control-allow-origin"] == "https://fie.pages.dev"
        denied = await client.get("/healthz", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in denied.headers


async def test_progress_is_reported_while_a_run_is_in_flight(api_settings: Settings) -> None:
    hold = asyncio.Event()
    engine = FakeEngine(hold=hold)
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        ).json()["job_id"]
        running = await wait_for_status(client, job_id, headers=AUTH, until={"running"})
        # The fake engine writes no checkpoint, so there is nothing to report
        # — and the endpoint says so rather than inventing a phase.
        assert running["progress"] is None
        hold.set()
        await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})


@pytest.mark.parametrize(
    ("channels", "awaiting", "expected"),
    [
        ({}, False, "starting"),
        ({"sub_questions": ["sq"]}, False, "researching"),
        ({"sub_questions": ["sq"], "draft_answer": "text"}, False, "synthesizing"),
        (
            {"sub_questions": ["sq"], "draft_answer": "text", "claims": ["c"], "verdicts": ["v"]},
            False,
            "verifying",
        ),
        ({"sub_questions": ["sq"], "draft_answer": "text"}, True, "reviewing"),
    ],
)
def test_the_phase_is_derived_from_our_own_state_channels(
    channels: dict[str, Any], awaiting: bool, expected: str
) -> None:
    """Not from LangGraph's queued-task bookkeeping, which is an internal
    naming convention: a progress field that silently goes blank on a library
    upgrade is worse than one built from state we define ourselves. The last
    two rows are the ordering that matters — a verify→research loop-back
    leaves `draft_answer` set, and reads as `verifying` rather than reverting."""
    assert _phase(channels, awaiting_review=awaiting) == expected
