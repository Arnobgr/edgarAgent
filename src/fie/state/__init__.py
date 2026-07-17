"""Typed state: the Pydantic models carried through the LangGraph run.

Later phases extend these models; they never replace them. Fields already
named in the blueprint but unused in Phase 0 (`sub_questions`, `claims`,
`verdicts`, `rejection_feedback`) are declared with empty defaults so the
schema stays stable across phases. `claims`/`verdicts` become typed models
in Phase 1 (Claim, Verdict); until then they hold plain dicts.
"""

from pydantic import BaseModel, Field


class EvidenceChunk(BaseModel):
    """One retrievable slice of a filing, carrying its full citation trail.

    `metadata` carries at minimum `accession_no` and `filing_date` — this is
    the citation trail (blueprint §5).
    """

    chunk_id: str
    doc_id: str
    ticker: str
    form_type: str
    section: str
    text: str
    score: float | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class RunBudget(BaseModel):
    """Per-run token/cost budget. Placeholder fields in Phase 0; real
    enforcement (caps, abort-on-exceed) arrives in Phase 1."""

    max_tokens: int = 500_000
    max_cost_usd: float = 5.0
    tokens_used: int = 0
    cost_usd_used: float = 0.0


class EngineState(BaseModel):
    """The LangGraph state schema for one run of the engine."""

    question: str
    evidence: list[EvidenceChunk] = Field(default_factory=list)
    draft_answer: str | None = None
    run_budget: RunBudget = Field(default_factory=RunBudget)

    # Declared now, used from Phase 1/2 on (stable schema across phases).
    sub_questions: list[str] = Field(default_factory=list)
    claims: list[dict[str, object]] = Field(default_factory=list)
    verdicts: list[dict[str, object]] = Field(default_factory=list)
    rejection_feedback: list[str] = Field(default_factory=list)
