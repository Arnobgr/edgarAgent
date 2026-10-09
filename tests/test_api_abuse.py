"""The abuse controls, offline.

These guard real money on a public endpoint, so they get the same rigor as
the graph invariants. Each test names the layer it holds
(`plans/phase-4-backend-and-deploy.md`, deliverable 1) and asserts the
property that layer exists for — not merely that the code runs.

The one to read first is
`test_turnstile_refuses_a_submission_carrying_a_valid_api_key`: it encodes
the sentence the whole design rests on, that on the public path the API key
identifies rather than controls.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from fie.api import create_app
from fie.api.governor import SpendTotals, cache_key
from fie.api.store import ApiStore
from fie.config import Settings
from tests.conftest import API_KEY
from tests.fakes import FakeEngine, api_client, make_result, wait_for_status

AUTH = {"X-API-Key": API_KEY}
DAY_ONE = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


class FakeClock:
    """A clock the tests move by hand, so the day-boundary reset is asserted
    rather than waited for."""

    def __init__(self, now: datetime = DAY_ONE) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


# --- Layer 5: the global spend governor ---


async def test_the_spend_governor_refuses_once_the_day_cap_is_crossed_and_stays_closed(
    api_settings: Settings,
) -> None:
    clock = FakeClock()
    settings = api_settings.model_copy(update={"spend_cap_daily_usd": 0.10})
    engine = FakeEngine()
    app = create_app(settings, run_fn=engine, clock=clock)
    async with api_client(app) as client:
        app.state.services.governor.record(0.09)
        under = await client.post("/v1/questions", json={"question": "First?"}, headers=AUTH)
        assert under.status_code == 202

        app.state.services.governor.record(0.02)
        for question in ("Second?", "Third?"):
            over = await client.post("/v1/questions", json={"question": question}, headers=AUTH)
            assert over.status_code == 429
            assert "daily spend cap reached" in over.json()["detail"]

        # …and it opens again on the next UTC day, without a restart.
        clock.advance(days=1)
        tomorrow = await client.post("/v1/questions", json={"question": "Fourth?"}, headers=AUTH)
        assert tomorrow.status_code == 202


def test_the_month_warning_leads_exhaustion_rather_than_reporting_it() -> None:
    """The warning is only worth having if it fires while there is still a
    decision to make.

    At $4.00 of $5.00 the service is answering normally and `exhausted` is
    false — that gap is the whole feature. An alarm keyed on `exhausted`
    reports history: the demo is already dark, and the only choice left is
    which month to be sorry about.

    The day cap is deliberately absent from this. It reopens at midnight UTC
    with nobody doing anything, so alerting on it would page for the system
    working as designed.
    """

    def at(day_usd: float, month_usd: float) -> SpendTotals:
        return SpendTotals(
            day="2026-08-07",
            month="2026-08",
            day_usd=day_usd,
            month_usd=month_usd,
            day_cap_usd=0.35,
            month_cap_usd=5.0,
        )

    assert at(0.0, 3.99).month_nearly_spent is False
    assert at(0.0, 4.00).month_nearly_spent is True
    assert at(0.0, 4.00).exhausted is False
    assert at(0.0, 5.00).exhausted is True

    # A spent day says nothing about the month.
    assert at(0.35, 0.35).exhausted is True
    assert at(0.35, 0.35).month_nearly_spent is False


async def test_healthz_reports_the_month_warning_without_touching_status(
    api_settings: Settings,
) -> None:
    """`spend_ok` crosses while questions are still being accepted, and
    `status` does not move with it.

    Spend is a budget state, not a liveness one. Folding them together gives
    a monitor that either pages for a working service or — once the two are
    one number — hides a dead one behind a full wallet. So they are two
    fields and, in `docs/monitoring.md`, two monitors.
    """
    settings = api_settings.model_copy(
        update={"spend_cap_daily_usd": 5.0, "spend_cap_monthly_usd": 5.0}
    )
    app = create_app(settings, run_fn=FakeEngine(), clock=FakeClock())
    async with api_client(app) as client:
        governor = app.state.services.governor
        before = (await client.get("/healthz")).json()
        assert before["spend_ok"] is True

        governor.record(3.90)
        assert (await client.get("/healthz")).json()["spend_ok"] is True

        governor.record(0.20)  # $4.10 of $5.00
        after = (await client.get("/healthz")).json()
        assert after["spend_ok"] is False
        assert governor.totals().exhausted is False

        # The warning fired; the thing it warns about has not happened.
        still_open = await client.post("/v1/questions", json={"question": "Still?"}, headers=AUTH)
        assert still_open.status_code == 202

        # Qdrant is unreachable offline, so both readings are `degraded` for
        # that reason — the assertion that matters is that crossing the spend
        # line moved `spend_ok` and left every other field where it was.
        assert before["status"] == after["status"]
        assert (before["qdrant"], before["facts_store"]) == (
            after["qdrant"],
            after["facts_store"],
        )


async def test_the_month_cap_holds_on_a_day_that_is_otherwise_clear(
    api_settings: Settings,
) -> None:
    """One bad day costs 7% of the month; a bad month still stops."""
    clock = FakeClock()
    settings = api_settings.model_copy(
        update={"spend_cap_daily_usd": 0.35, "spend_cap_monthly_usd": 1.0}
    )
    app = create_app(settings, run_fn=FakeEngine(), clock=clock)
    async with api_client(app) as client:
        for _ in range(4):
            app.state.services.governor.record(0.30)
            clock.advance(days=1)
        assert app.state.services.governor.totals().day_usd == 0.0

        response = await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        assert response.status_code == 429
        assert "monthly spend cap reached" in response.json()["detail"]


async def test_a_run_that_raised_is_still_charged_to_the_governor(
    api_settings: Settings,
) -> None:
    """The caps live inside `answer_question`, whose tracker is only
    reachable through a returned `RunResult`. A run that raises has none and
    the tokens were still bought, so the registry rides a second tracker
    along as a callback — otherwise the one path where spending went wrong is
    the one path the governor cannot see."""
    engine = FakeEngine(raises=RuntimeError("boom"), usage=(1_000_000, 1_000_000, "glm-5.2"))
    app = create_app(api_settings, run_fn=engine)
    async with api_client(app) as client:
        job_id = (
            await client.post("/v1/questions", json={"question": "Anything?"}, headers=AUTH)
        ).json()["job_id"]
        await wait_for_status(client, job_id, headers=AUTH, until={"failed"})

        spend = await client.get("/v1/admin/spend", headers=AUTH)
        assert spend.json()["day_usd"] == pytest.approx(0.057 + 4.30)
        assert spend.json()["exhausted"] is True


# --- Layer 6: the per-IP daily quota ---


async def test_the_quota_counts_per_address_and_resets_on_the_day_boundary(
    api_settings: Settings,
) -> None:
    clock = FakeClock()
    settings = api_settings.model_copy(update={"public_daily_quota_per_ip": 2})
    app = create_app(settings, run_fn=FakeEngine(), clock=clock)
    async with api_client(app) as client:
        mine = {**AUTH, "X-Real-IP": "203.0.113.7"}
        theirs = {**AUTH, "X-Real-IP": "198.51.100.4"}
        # Distinct questions throughout: an exact repeat is served from cache
        # and never reaches the quota, which is the design (a cached answer
        # costs nothing, so it must not spend the caller's live runs).
        for i in range(2):
            assert (
                await client.post("/v1/questions", json={"question": f"Q{i}?"}, headers=mine)
            ).status_code == 202
        refused = await client.post("/v1/questions", json={"question": "Q2?"}, headers=mine)
        assert refused.status_code == 429
        assert "recorded runs stay available" in refused.json()["detail"]

        # Another address is unaffected — the limit is per caller, not global.
        assert (
            await client.post("/v1/questions", json={"question": "Q3?"}, headers=theirs)
        ).status_code == 202

        clock.advance(days=1)
        assert (
            await client.post("/v1/questions", json={"question": "Q4?"}, headers=mine)
        ).status_code == 202


async def test_the_quota_reads_x_real_ip_not_the_forwarded_chain(api_settings: Settings) -> None:
    """nginx *appends* to `X-Forwarded-For`, so its first entry is
    attacker-controlled: a quota keyed on it counts nothing. `X-Real-IP` is
    overwritten with `$remote_addr` on every request."""
    settings = api_settings.model_copy(update={"public_daily_quota_per_ip": 1})
    async with api_client(create_app(settings, run_fn=FakeEngine())) as client:
        headers = {**AUTH, "X-Real-IP": "203.0.113.7"}
        assert (
            await client.post("/v1/questions", json={"question": "Q0?"}, headers=headers)
        ).status_code == 202
        spoofed = await client.post(
            "/v1/questions",
            json={"question": "Q1?"},
            headers={**headers, "X-Forwarded-For": "10.9.9.9"},
        )
        assert spoofed.status_code == 429


# --- Layer 3: Turnstile ---


async def test_turnstile_refuses_a_submission_carrying_a_valid_api_key(
    api_settings: Settings,
) -> None:
    """The assertion that encodes "the SPA key is not a control": the Phase 5
    frontend ships its key in the bundle where devtools can read it, so a
    correct `X-API-Key` must not be enough on the public path."""
    settings = api_settings.model_copy(
        update={"turnstile_required": True, "turnstile_secret_key": SecretStr("ts-secret")}
    )
    engine = FakeEngine()
    async with api_client(create_app(settings, run_fn=engine)) as client:
        response = await client.post("/v1/questions", json={"question": "Q?"}, headers=AUTH)
        assert response.status_code == 403
        assert "Turnstile" in response.json()["detail"]
    assert engine.calls == []


async def test_turnstile_fails_closed_when_siteverify_cannot_be_reached(
    api_settings: Settings,
) -> None:
    """A demo that is briefly unavailable is cheaper than one that is briefly
    free to script. `turnstile_verify_url` points at a closed port here."""
    settings = api_settings.model_copy(
        update={"turnstile_required": True, "turnstile_secret_key": SecretStr("ts-secret")}
    )
    async with api_client(create_app(settings, run_fn=FakeEngine())) as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "Q?", "turnstile_token": "a-token-the-widget-minted"},
            headers=AUTH,
        )
        assert response.status_code == 403


# --- Layer 7: the concurrency semaphore and the queue cap ---


async def test_the_semaphore_admits_at_most_the_configured_number_of_runs(
    api_settings: Settings,
) -> None:
    """The OOM control, sized to the container memory limits: at most N
    graphs hold evidence, claims and a client at once."""
    hold = asyncio.Event()
    engine = FakeEngine(hold=hold)
    settings = api_settings.model_copy(
        update={
            "api_max_concurrent_runs": 1,
            "api_max_queued_runs": 5,
            "public_daily_quota_per_ip": 50,
        }
    )
    async with api_client(create_app(settings, run_fn=engine)) as client:
        job_ids = [
            (await client.post("/v1/questions", json={"question": f"Q{i}?"}, headers=AUTH)).json()[
                "job_id"
            ]
            for i in range(3)
        ]
        for _ in range(20):
            await asyncio.sleep(0)
        assert engine.peak_concurrent == 1

        hold.set()
        for job_id in job_ids:
            await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})
    assert len(engine.calls) == 3


async def test_the_queue_cap_refuses_rather_than_queueing_without_bound(
    api_settings: Settings,
) -> None:
    """A job that will not start for twenty minutes is a failure wearing a
    `202`."""
    hold = asyncio.Event()
    engine = FakeEngine(hold=hold)
    settings = api_settings.model_copy(
        update={
            "api_max_concurrent_runs": 1,
            "api_max_queued_runs": 1,
            "public_daily_quota_per_ip": 50,
        }
    )
    async with api_client(create_app(settings, run_fn=engine)) as client:
        job_ids = []
        for i in range(2):
            accepted = await client.post("/v1/questions", json={"question": f"Q{i}?"}, headers=AUTH)
            assert accepted.status_code == 202
            job_ids.append(accepted.json()["job_id"])

        full = await client.post("/v1/questions", json={"question": "Q2?"}, headers=AUTH)
        assert full.status_code == 429
        assert "at capacity" in full.json()["detail"]

        # And it opens again as soon as a slot frees, without a restart.
        hold.set()
        for job_id in job_ids:
            await wait_for_status(client, job_id, headers=AUTH, until={"succeeded"})
        assert (
            await client.post("/v1/questions", json={"question": "Q3?"}, headers=AUTH)
        ).status_code == 202


# --- Layer 8: the exact-match answer cache ---


async def test_a_repeated_question_is_served_from_cache_and_costs_nothing(
    api_settings: Settings,
) -> None:
    engine = FakeEngine(default=make_result(answer="Net sales were $391.0 billion [c1]."))
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        first = await client.post(
            "/v1/questions", json={"question": "What were Apple's net sales?"}, headers=AUTH
        )
        await wait_for_status(client, first.json()["job_id"], headers=AUTH, until={"succeeded"})

        # Same question, different capitalisation and spacing: the key
        # normalises those and nothing else.
        second = await client.post(
            "/v1/questions", json={"question": "  what were APPLE's   net sales? "}, headers=AUTH
        )
        assert second.json()["source"] == "cached"
        assert second.json()["status"] == "succeeded"

        status = await client.get(f"/v1/jobs/{second.json()['job_id']}", headers=AUTH)
        assert status.json()["cost_usd"] == 0.0
        result = await client.get(f"/v1/jobs/{second.json()['job_id']}/result", headers=AUTH)
        assert result.json()["source"] == "cached"
        # The payload carries the *original* run's id — saying so is the
        # honest thing to report.
        assert result.json()["result"]["run_id"] == first.json()["job_id"]

    assert len(engine.calls) == 1


async def test_the_cache_never_crosses_a_company_scope(api_settings: Settings) -> None:
    """A nearest-neighbour cache would serve the answer to a *different*
    question, which for a verification system is the worst failure mode on
    the menu. Scope is part of the key for the same reason."""
    engine = FakeEngine()
    async with api_client(create_app(api_settings, run_fn=engine)) as client:
        first = await client.post(
            "/v1/questions", json={"question": "How did revenue move?"}, headers=AUTH
        )
        await wait_for_status(client, first.json()["job_id"], headers=AUTH, until={"succeeded"})

        scoped = await client.post(
            "/v1/questions",
            json={"question": "How did revenue move?", "companies": ["AAPL"]},
            headers=AUTH,
        )
        assert scoped.json()["source"] == "live"
    assert len(engine.calls) == 2


async def test_a_cached_answer_does_not_spend_the_callers_quota(api_settings: Settings) -> None:
    """The quota exists to bound spend, and a cache hit spends nothing — so
    the visitor who reloads the same question keeps their live runs."""
    settings = api_settings.model_copy(update={"public_daily_quota_per_ip": 1})
    async with api_client(create_app(settings, run_fn=FakeEngine())) as client:
        headers = {**AUTH, "X-Real-IP": "203.0.113.7"}
        first = await client.post("/v1/questions", json={"question": "Q?"}, headers=headers)
        await wait_for_status(client, first.json()["job_id"], headers=AUTH, until={"succeeded"})

        for _ in range(3):
            repeat = await client.post("/v1/questions", json={"question": "Q?"}, headers=headers)
            assert repeat.json()["source"] == "cached"

        assert (
            await client.post("/v1/questions", json={"question": "New?"}, headers=headers)
        ).status_code == 429


def test_the_cache_key_normalises_case_and_whitespace_only() -> None:
    assert cache_key("Total  net SALES?\n", []) == cache_key("total net sales?", [])
    assert cache_key("q", ["AAPL", "TSLA"]) == cache_key("q", ["tsla", "aapl"])
    assert cache_key("q", ["AAPL"]) != cache_key("q", [])
    assert cache_key("net sales", []) != cache_key("net income", [])


async def test_a_cached_answer_is_served_on_a_day_the_spend_cap_is_spent(
    api_settings: Settings,
) -> None:
    """The cache is checked *before* the governor: a cached answer costs
    nothing, so a spent day should still be able to serve one."""
    settings = api_settings.model_copy(update={"spend_cap_daily_usd": 0.10})
    app = create_app(settings, run_fn=FakeEngine())
    async with api_client(app) as client:
        first = await client.post("/v1/questions", json={"question": "Q?"}, headers=AUTH)
        await wait_for_status(client, first.json()["job_id"], headers=AUTH, until={"succeeded"})

        app.state.services.governor.record(0.50)
        cached = await client.post("/v1/questions", json={"question": "Q?"}, headers=AUTH)
        assert cached.status_code == 202
        assert cached.json()["source"] == "cached"

        fresh = await client.post("/v1/questions", json={"question": "Different?"}, headers=AUTH)
        assert fresh.status_code == 429


# --- The ledger survives a restart ---


async def test_the_spend_ledger_survives_a_restart(api_settings: Settings) -> None:
    """An in-memory counter would reset the month's spend on every deploy."""
    store = ApiStore(api_settings.api_db_path)
    store.record_spend(DAY_ONE.date().isoformat(), 0.40)
    store.close()

    settings = api_settings.model_copy(update={"spend_cap_daily_usd": 0.35})
    async with api_client(create_app(settings, run_fn=FakeEngine(), clock=FakeClock())) as client:
        response = await client.post("/v1/questions", json={"question": "Q?"}, headers=AUTH)
        assert response.status_code == 429
