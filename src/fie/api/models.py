"""Request and response models — the HTTP contract.

`RunResult` and the grounding-table types from `fie.state` are reused rather
than restated: they are already the output contract the CLI prints and the
eval scores, and a parallel set of API-shaped copies would be two things to
keep in agreement.
"""

from typing import Literal

from pydantic import BaseModel, Field

from fie.api.store import JobStatus
from fie.state import GroundingRow, ReviewDecision, RunResult

# How the answer reached the caller. Kept explicit because the three are
# genuinely different promises: `live` was computed for this request,
# `cached` is a byte-identical earlier answer to the same question, and
# `recorded` was exported ahead of time and ships inside the frontend bundle.
# Phase 5 labels them honestly, so the label has to exist in the contract.
RunSource = Literal["live", "cached", "recorded"]

# Coarse enough to survive a graph change, specific enough to be worth
# rendering. `finalizing` covers retraction and the critic gate.
RunPhase = Literal[
    "starting", "researching", "synthesizing", "verifying", "reviewing", "finalizing"
]


class QuestionRequest(BaseModel):
    """The body of `POST /v1/questions`."""

    question: str = Field(min_length=1)
    companies: list[str] = Field(default_factory=list)
    # Minted by the Turnstile widget on the frontend. Absent on the tailnet
    # path, where `Settings.turnstile_required` is off.
    turnstile_token: str | None = None


class JobAccepted(BaseModel):
    """A `202`: the job id to poll and whether the answer cache served it."""

    job_id: str
    status: JobStatus
    source: RunSource


class Progress(BaseModel):
    """Where a running job has got to, read out of its LangGraph checkpoint.

    `phase` is derived from *our* state channels — which of `sub_questions`,
    `draft_answer` and `verdicts` have been filled in — rather than from
    LangGraph's queued-task bookkeeping. The queue is reachable (the
    checkpoint carries `branch:to:<node>` channels) but it is an internal
    naming convention, and a progress field that silently goes blank on a
    library upgrade is worse than one built from state we define ourselves.
    """

    step: int | None = None
    phase: RunPhase = "starting"
    verify_iteration: int = 0
    critic_iteration: int = 0
    cost_usd: float = 0.0
    tokens: int = 0


class JobStatusResponse(BaseModel):
    """What polling `GET /v1/jobs/{id}` returns while a run is in flight."""

    job_id: str
    status: JobStatus
    question: str
    companies: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    source: RunSource
    cost_usd: float
    tokens: int
    error: str | None = None
    progress: Progress | None = None


class RunPayload(BaseModel):
    """The final answer. Returned by `GET /v1/jobs/{id}/result` and written
    verbatim by `fie export-runs`, so Phase 5 renders a live run and a
    recorded one through one type."""

    job_id: str
    source: RunSource
    created_at: str
    result: RunResult


class ReviewResponse(BaseModel):
    """The claims a job is waiting on a human for."""

    job_id: str
    unresolved: list[GroundingRow] = Field(default_factory=list)


class ReviewSubmission(BaseModel):
    """Every decision at once.

    Deliberately a batch where the plan sketched one claim per call: the
    graph resumes from its interrupt exactly once, so a per-claim endpoint
    would apply the first decision, finish the run, and have nowhere to put
    the second. Claims left out of the list keep the status the verifier gave
    them (`review` in `fie.graph.build`).
    """

    decisions: list[ReviewDecision] = Field(default_factory=list)


class SpendResponse(BaseModel):
    """The governor's counters — the admin view behind the alert."""

    day: str
    month: str
    day_usd: float
    month_usd: float
    day_cap_usd: float
    month_cap_usd: float
    exhausted: bool


class HealthResponse(BaseModel):
    """Liveness plus the dependencies that are actually on the answer path.

    Two fields are reported and neither gates `status`, for opposite reasons.
    Langfuse is *below* the line: tracing is off the answer path, and letting
    an external SaaS turn this service red would page the operator for someone else's
    outage. `spend_ok` is *ahead of* it: it goes false while the service is
    still answering, so folding it into `status` would report an outage that
    has not happened, and it is watched by its own monitor instead
    (`docs/monitoring.md`).

    `spend_ok` is a boolean and not the figures, because this endpoint is
    unauthenticated. The figures live behind `/v1/admin/spend`.
    """

    status: Literal["ok", "degraded"]
    qdrant: bool
    facts_store: bool
    jobs_in_flight: int
    langfuse_configured: bool
    spend_ok: bool
