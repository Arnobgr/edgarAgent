"""The HTTP surface: submit a question, poll it, fetch the verified answer,
and rule on the claims the system could not resolve on its own.

Everything is injected the way `build_graph` is — `create_app(run_fn=...)`
lets the whole API be exercised against a fake engine with no network, no
keys and no corpus, which is the only way the abuse-control tests can be part
of `make test`.

**Where the enforcement actually lives.** `X-API-Key` authenticates the
tailnet consumer; it is not what holds the public path (see
`fie.api.security`). The public path is held by nginx (load) and by
Turnstile, the spend governor, the per-IP quota and the queue cap (spend).
The ordering of those checks in `submit_question` is deliberate and
commented there.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from starlette.middleware.base import RequestResponseEndpoint

from fie.api.governor import AnswerCache, IpQuota, SpendGovernor
from fie.api.jobs import JobRegistry, RunFn
from fie.api.logs import configure_logging
from fie.api.models import (
    HealthResponse,
    JobAccepted,
    JobStatusResponse,
    QuestionRequest,
    ReviewResponse,
    ReviewSubmission,
    RunPayload,
    RunSource,
    SpendResponse,
)
from fie.api.progress import read_progress
from fie.api.security import api_key_valid, verify_turnstile
from fie.api.store import ApiStore, JobRow, utcnow
from fie.config import Settings, get_settings
from fie.observability import new_run_id, trace_id_for
from fie.state import UNRESOLVED_STATUSES, ReviewDecision, RunResult

logger = logging.getLogger(__name__)
log = structlog.get_logger(__name__)


@dataclass
class Services:
    """Everything the handlers need, built once in the lifespan."""

    settings: Settings
    store: ApiStore
    governor: SpendGovernor
    quota: IpQuota
    cache: AnswerCache
    registry: JobRegistry
    saver: AsyncSqliteSaver
    http: httpx.AsyncClient


def services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


def client_ip(request: Request) -> str:
    """The address the per-IP quota counts.

    `X-Real-IP`, not `X-Forwarded-For`. nginx is configured with
    `proxy_add_x_forwarded_for`, which *appends* to whatever the client sent,
    so the first entry of that header is attacker-controlled and a per-IP
    quota keyed on it counts nothing. `X-Real-IP` is overwritten by nginx
    with `$remote_addr` on every request. The header is only trustworthy
    because the app binds to loopback and nginx is the sole route in — see
    `deploy/nginx/`.
    """
    forwarded = request.headers.get("X-Real-IP")
    if forwarded:
        return forwarded.strip()
    return request.client.host if request.client else "unknown"


def _production_run_fn(settings: Settings) -> RunFn:
    """Bind `answer_question` to the public-path caps.

    Tightened here, once: the tailnet path is the operator driving their own machine
    and keeps the loose caps, but everything arriving over HTTP is a stranger
    and gets `public_run_settings()`.
    """
    from fie.runner import answer_question

    public = settings.public_run_settings()

    async def run(
        *,
        question: str | None,
        run_id: str,
        review_decisions: list[ReviewDecision] | None,
        extra_callbacks: list[BaseCallbackHandler],
    ) -> RunResult:
        return await answer_question(
            question=question,
            run_id=run_id,
            settings=public,
            run_name="fie-api",
            extra_callbacks=extra_callbacks,
            review_mode=settings.api_review_mode,
            review_decisions=review_decisions,
        )

    return run


def _check_startup_config(settings: Settings) -> None:
    """Refuse to boot in a configuration that silently disables a control.

    An API that authenticates everyone because an env var went missing is
    worse than one that is down: it looks like it is working. Same for
    Turnstile — `turnstile_required` with no secret would fail open on the
    one layer holding the public path.
    """
    if not settings.api_key.get_secret_value():
        raise RuntimeError(
            "API_KEY is not set. The service refuses to start without one — "
            "set it in .env for local runs, or mount it as a compose secret."
        )
    if settings.turnstile_required and not settings.turnstile_secret_key.get_secret_value():
        raise RuntimeError(
            "TURNSTILE_REQUIRED is on but TURNSTILE_SECRET_KEY is empty: the check "
            "would pass every submission. Set the secret or turn the flag off."
        )


def create_app(
    settings: Settings | None = None,
    *,
    run_fn: RunFn | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> FastAPI:
    resolved = settings or get_settings()
    _check_startup_config(resolved)
    configure_logging(json_logs=resolved.api_log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = ApiStore(resolved.api_db_path)
        governor = SpendGovernor(
            store,
            daily_cap_usd=resolved.spend_cap_daily_usd,
            monthly_cap_usd=resolved.spend_cap_monthly_usd,
            clock=clock,
        )
        cache = AnswerCache(store)
        http = httpx.AsyncClient()
        checkpoint_path = resolved.data_dir / "checkpoints.db"
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            app.state.services = Services(
                settings=resolved,
                store=store,
                governor=governor,
                quota=IpQuota(store, daily_limit=resolved.public_daily_quota_per_ip, clock=clock),
                cache=cache,
                registry=JobRegistry(
                    store=store,
                    run_fn=run_fn or _production_run_fn(resolved),
                    governor=governor,
                    cache=cache,
                    model_prices=resolved.model_prices,
                    max_concurrent=resolved.api_max_concurrent_runs,
                    max_queued=resolved.api_max_queued_runs,
                ),
                saver=saver,
                http=http,
            )
            # Jobs the previous process was still working on. Their asyncio
            # tasks died with it; the LangGraph checkpoint did not, so they
            # are offered for resume rather than written off.
            orphans = store.recover_orphans()
            if orphans:
                log.warning("recovered orphaned jobs", count=len(orphans), job_ids=orphans)
            try:
                yield
            finally:
                await app.state.services.registry.shutdown()
                await http.aclose()
                store.close()

    app = FastAPI(
        title="Financial Intelligence Engine",
        version="0.1.0",
        summary="Verified answers over SEC filings, with a grounding table per claim.",
        lifespan=lifespan,
    )
    if resolved.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            # Exactly the Pages origin. The frontend and the API are on
            # different hosts, so every browser call is cross-origin; "*"
            # would hand the same access to any page on the internet.
            allow_origins=resolved.api_cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["X-API-Key", "Content-Type"],
        )

    @app.middleware("http")
    async def bind_request_context(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex[:12]
        structlog.contextvars.bind_contextvars(
            request_id=request_id, path=request.url.path, method=request.method
        )
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "path", "method")
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(_v1_router(), prefix="/v1", dependencies=[Depends(require_api_key)])

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz(request: Request) -> HealthResponse:
        """Liveness plus the dependencies on the answer path.

        Unauthenticated on purpose: the external prober is what tells the operator
        the box is down, and it cannot hold a secret. It reveals nothing an
        attacker could not learn by submitting a question.
        """
        svc = services(request)
        qdrant_ok = False
        try:
            response = await svc.http.get(f"{svc.settings.qdrant_url}/readyz", timeout=2.0)
            qdrant_ok = response.status_code == status.HTTP_200_OK
        except Exception:
            logger.warning("Qdrant health check failed", exc_info=True)
        facts_ok = svc.settings.ingest_db_path.exists()
        return HealthResponse(
            status="ok" if qdrant_ok and facts_ok else "degraded",
            qdrant=qdrant_ok,
            facts_store=facts_ok,
            jobs_in_flight=svc.registry.in_flight,
            # Informational, never in the pass/fail: tracing is off the
            # answer path, and letting a SaaS outage turn this red would page
            # the operator for someone else's problem.
            langfuse_configured=bool(svc.settings.langfuse_public_key),
        )

    return app


async def require_api_key(request: Request) -> None:
    if not api_key_valid(request.headers.get("X-API-Key"), services(request).settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-API-Key"
        )


def _v1_router() -> APIRouter:
    router = APIRouter()

    @router.post("/questions", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
    async def submit_question(request: Request, payload: QuestionRequest) -> JobAccepted:
        """Accept a question and start a run.

        The check order is the design, not habit:

        1. **Input constraints** — free, and a 10 kB question should never
           reach a model.
        2. **Turnstile** — before anything that costs or counts, so a bot
           never consumes a real visitor's quota.
        3. **Cache** — before the spend governor, because a cached answer
           costs nothing and should still be served on a day the cap is
           spent. This is the layer that removes the most load for the least
           code.
        4. **Spend governor**, then **per-IP quota**, then **queue capacity**
           — global money first, then this caller's share, then whether
           there is room to run it at all.
        """
        svc = services(request)
        settings = svc.settings
        question = payload.question.strip()
        if not question:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "question is empty")
        if len(question) > settings.question_max_chars:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"question exceeds {settings.question_max_chars} characters",
            )
        companies = [c.strip().upper() for c in payload.companies if c.strip()]
        unknown = sorted(set(companies) - set(settings.corpus_tickers))
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"outside the ingested corpus: {', '.join(unknown)}. "
                f"Available: {', '.join(settings.corpus_tickers)}",
            )

        ip = client_ip(request)
        if not await verify_turnstile(
            payload.turnstile_token, client=svc.http, settings=settings, remote_ip=ip
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Turnstile verification failed or was not attempted"
            )

        job_id = new_run_id()
        cached = svc.cache.get(question, companies)
        if cached is not None:
            svc.store.create_job(
                job_id=job_id, question=question, companies=companies, client_ip=ip
            )
            result = RunResult.model_validate_json(cached)
            svc.store.save_result(
                job_id,
                status="succeeded",
                result_json=cached,
                cost_usd=0.0,
                tokens=0,
                cached=True,
            )
            log.info("cache hit", job_id=job_id, source_run=result.run_id)
            return JobAccepted(job_id=job_id, status="succeeded", source="cached")

        refusal = svc.governor.refusal()
        if refusal:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, refusal)
        if not svc.quota.take(ip):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"daily limit of {settings.public_daily_quota_per_ip} live runs reached "
                "for this address; recorded runs stay available",
            )
        if not svc.registry.has_capacity():
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "the engine is at capacity; try again in a few minutes",
            )

        svc.store.create_job(job_id=job_id, question=question, companies=companies, client_ip=ip)
        svc.registry.launch(job_id, question=_scoped_question(question, companies))
        log.info("job accepted", job_id=job_id, companies=companies)
        return JobAccepted(job_id=job_id, status="queued", source="live")

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    async def job_status(request: Request, job_id: str) -> JobStatusResponse:
        svc = services(request)
        row = _require_job(svc, job_id)
        progress = None
        if row.status in ("running", "awaiting_review"):
            progress = await read_progress(
                svc.saver, job_id, awaiting_review=row.status == "awaiting_review"
            )
        return JobStatusResponse(
            job_id=row.id,
            status=row.status,
            question=row.question,
            companies=row.companies,
            created_at=row.created_at,
            updated_at=row.updated_at,
            source=_source(row),
            cost_usd=row.cost_usd,
            tokens=row.tokens,
            error=row.error,
            progress=progress,
        )

    @router.get("/jobs/{job_id}/result", response_model=RunPayload)
    async def job_result(request: Request, job_id: str) -> RunPayload:
        svc = services(request)
        row = _require_job(svc, job_id)
        if row.result_json is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"job is {row.status}; no result yet",
            )
        return RunPayload(
            job_id=row.id,
            source=_source(row),
            created_at=row.created_at,
            result=RunResult.model_validate_json(row.result_json),
        )

    @router.get("/jobs/{job_id}/review", response_model=ReviewResponse)
    async def review_queue(request: Request, job_id: str) -> ReviewResponse:
        svc = services(request)
        row = _require_job(svc, job_id)
        result = _require_awaiting_review(row)
        return ReviewResponse(
            job_id=row.id,
            unresolved=[r for r in result.grounding if r.status in UNRESOLVED_STATUSES],
        )

    @router.post("/jobs/{job_id}/review", status_code=status.HTTP_202_ACCEPTED)
    async def submit_review(
        request: Request, job_id: str, payload: ReviewSubmission
    ) -> JobAccepted:
        """Apply the reviewer's decisions and resume the run.

        One call, all decisions: the graph resumes from its interrupt exactly
        once (see `ReviewSubmission`)."""
        svc = services(request)
        row = _require_job(svc, job_id)
        result = _require_awaiting_review(row)
        open_ids = {r.claim_id for r in result.grounding if r.status in UNRESOLVED_STATUSES}
        unknown = sorted({d.claim_id for d in payload.decisions} - open_ids)
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"not awaiting review: {', '.join(unknown)}",
            )
        svc.store.set_status(job_id, "running")
        svc.registry.launch(job_id, question=None, review_decisions=payload.decisions)
        log.info("review submitted", job_id=job_id, decisions=len(payload.decisions))
        return JobAccepted(job_id=job_id, status="running", source="live")

    @router.post("/jobs/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    async def resume_job(request: Request, job_id: str) -> JobAccepted:
        """Restart a job the previous process died on, from its checkpoint."""
        svc = services(request)
        row = _require_job(svc, job_id)
        if row.status != "interrupted":
            raise HTTPException(status.HTTP_409_CONFLICT, f"job is {row.status}, not interrupted")
        if not svc.registry.has_capacity():
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "the engine is at capacity; try again shortly"
            )
        svc.store.set_status(job_id, "running")
        svc.registry.launch(job_id, question=None)
        return JobAccepted(job_id=job_id, status="running", source="live")

    @router.get("/admin/spend", response_model=SpendResponse)
    async def spend(request: Request) -> SpendResponse:
        """Day and month totals — alert on this *before* the cap, not on it:
        hitting the provider-side hard cap silently means the demo is down."""
        totals = services(request).governor.totals()
        return SpendResponse(
            day=totals.day,
            month=totals.month,
            day_usd=totals.day_usd,
            month_usd=totals.month_usd,
            day_cap_usd=totals.day_cap_usd,
            month_cap_usd=totals.month_cap_usd,
            exhausted=totals.exhausted,
        )

    @router.get("/jobs/{job_id}/trace")
    async def job_trace(request: Request, job_id: str) -> dict[str, str | None]:
        """The Langfuse trace id for a job, derived from its run id."""
        svc = services(request)
        _require_job(svc, job_id)
        configured = bool(svc.settings.langfuse_public_key)
        return {"trace_id": trace_id_for(job_id) if configured else None}

    return router


def _scoped_question(question: str, companies: list[str]) -> str:
    """Fold an explicit company scope into the question text.

    The graph takes one string. Rather than add a scope channel that only the
    HTTP path would ever set, the constraint is stated where the decomposer
    and the retriever already read — in the question."""
    if not companies:
        return question
    return f"{question}\n\n(Restrict the answer to: {', '.join(companies)}.)"


def _source(row: JobRow) -> RunSource:
    return "cached" if row.cached else "live"


def _require_job(svc: Services, job_id: str) -> JobRow:
    row = svc.store.get_job(job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown job {job_id}")
    return row


def _require_awaiting_review(row: JobRow) -> RunResult:
    if row.status != "awaiting_review" or row.result_json is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is {row.status}, not awaiting review")
    return RunResult.model_validate_json(row.result_json)
