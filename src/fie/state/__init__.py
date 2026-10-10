"""Typed state: the Pydantic models carried through the LangGraph run.

Phase 1 made `claims`/`verdicts` typed (Claim, Verdict), added the loop
bookkeeping (`verify_iteration`, `critic_iteration`, `rejection_feedback`),
the error channel for clean aborts, the structured-output schemas the LLM
nodes emit, and the `RunResult` contract the Phase 4 API and Phase 5
frontend render.

Phase 2 adds the fan-out machinery: `SubQuestion` and the `ResearchBranch`
Send payload, and turns the channels that parallel research branches write
concurrently (`evidence`, `branch_results`, `run_budget`, `error`) into
reducer channels so branch updates merge instead of clobbering.
"""

import operator
from typing import Annotated, Literal

from pydantic import BaseModel, Field

ClaimType = Literal["numeric", "qualitative", "forward_looking"]
# `accepted` is the human-in-the-loop verdict and is deliberately NOT
# `grounded`: a reviewer waving a claim through is not the machine finding a
# source for it, and collapsing the two would let the review gate launder
# unverified text into `grounding_rate` (Phase 4, ADR 0034). The verdict on
# the row stays whatever the verifier ruled, so the metrics still see the
# truth; only the claim's fate in the prose changes.
ClaimStatus = Literal["pending", "grounded", "unsupported", "partial", "retracted", "accepted"]
VerdictLabel = Literal["grounded", "unsupported", "partial"]
VerifyMethod = Literal["numeric_reconciliation", "semantic", "attribution"]
SubQuestionStatus = Literal["open", "researched", "exhausted"]
# What a reviewer may decide about one unresolved claim at the HITL gate.
ReviewAction = Literal["accept", "retract"]
# When the gate fires. `unresolved` is the product behaviour (a clean run is
# never held up for a human); `always` exists so a demo can show the gate on
# a question that happens to verify cleanly. `off` is what the eval runs, so
# the gate can never move a gate metric.
ReviewMode = Literal["off", "unresolved", "always"]
# The claim statuses the gate presents for review. `partial` is included
# because a partially-supported claim is exactly the judgement call a human
# is better at than the rubric.
UNRESOLVED_STATUSES: tuple[ClaimStatus, ...] = ("unsupported", "partial")

# Part of the answer contract: the retract node appends this section listing
# excised claims, so consumers (metrics, renderers) can split the answer
# proper from the retraction disclosure. Defined here, not in graph.build,
# because eval metrics must find it without importing the graph (ADR 0015).
RETRACTED_HEADER = "### Retracted claims"


class EvidenceChunk(BaseModel):
    """One retrievable slice of a filing, carrying its full citation trail.

    `metadata` carries at minimum `accession_no` and `filing_date` — this is
    the citation trail (blueprint §5). `sub_question_id` records which
    research branch retrieved the chunk (Phase 2 fan-out).
    """

    chunk_id: str
    doc_id: str
    ticker: str
    form_type: str
    section: str
    text: str
    score: float | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    sub_question_id: str | None = None


class SubQuestion(BaseModel):
    """One independent part of the analyst question, as split by the
    decomposer. A trivial question passes through as a single sub-question
    carrying the original wording verbatim."""

    id: str
    text: str
    rationale: str = ""
    status: SubQuestionStatus = "open"


class BranchResult(BaseModel):
    """Outcome of one research fan-out branch (accumulating channel entry).

    Branches never write `sub_questions` directly — concurrent writes to an
    overwrite channel would collide — so each branch reports here and the
    fan-in stamps statuses from the latest entry per sub-question."""

    sub_question_id: str
    ok: bool
    detail: str | None = None
    # What the branch actually searched with. Without it a retrieval failure
    # is indistinguishable from a rewrite failure, and the next diagnosis
    # session pays for that (ADR 0022). This channel already has a reducer,
    # so recording it here costs no new state machinery.
    query: str | None = None


class ResearchBranch(BaseModel):
    """The Send payload for one research branch: the sub-question to
    research, an optional query override (the failed claim's text on a
    targeted loop-back) and the verifier's rejection feedback."""

    sub_question: SubQuestion
    query: str | None = None
    feedback: str | None = None
    # Branches never carry a run error; the field exists so the guardrail
    # wrapper can treat branch payloads like any other node state.
    error: str | None = None


class Claim(BaseModel):
    """One discrete factual claim decomposed from the draft answer."""

    id: str
    text: str
    claim_type: ClaimType
    source_chunk_ids: list[str] = Field(default_factory=list)
    status: ClaimStatus = "pending"
    sub_question_id: str | None = None
    # Verbatim from the draft answer; see `DraftClaim.answer_span`.
    answer_span: str = ""


class Verdict(BaseModel):
    """The verifier's ruling on one claim. `feedback` explains a failure and
    drives re-retrieval on the loop-back."""

    claim_id: str
    verdict: VerdictLabel
    supporting_span: str | None = None
    method: VerifyMethod
    feedback: str | None = None


class ReviewDecision(BaseModel):
    """One reviewer ruling at the human-in-the-loop gate.

    Decisions travel as a *list* of these, never as a `{claim_id: action}`
    mapping, and that is load-bearing rather than stylistic. LangGraph reads
    a `dict` resume value as a map of *interrupt ids* to resume values when
    every key looks like one (`_loop.py`, `resume_is_map`) — and `all()` over
    an empty dict is `True`, so resuming with `{}` (a reviewer who accepted
    nothing) is read as an empty interrupt map and re-raises the interrupt
    instead of resuming. A list can never be mistaken for that map.
    """

    claim_id: str
    action: ReviewAction


class RunBudget(BaseModel):
    """Per-run token/cost budget, enforced by the Phase 1 guardrails: usage
    accumulates per LLM call; exceeding a cap aborts the run cleanly."""

    max_tokens: int = 500_000
    max_cost_usd: float = 5.0
    tokens_used: int = 0
    cost_usd_used: float = 0.0


# --- Reducers for channels that parallel branches write concurrently ---


def merge_evidence(existing: list[EvidenceChunk], new: list[EvidenceChunk]) -> list[EvidenceChunk]:
    """Accumulate evidence across branches and loop-backs, deduping per
    (sub_question_id, chunk_id) so a re-run branch doesn't duplicate its own
    chunks. The same chunk retrieved by two branches keeps both entries —
    prompt-level dedupe happens in context packing, not here."""
    merged: dict[tuple[str | None, str], EvidenceChunk] = {}
    for chunk in [*existing, *new]:
        merged.setdefault((chunk.sub_question_id, chunk.chunk_id), chunk)
    return list(merged.values())


def merge_budgets(existing: RunBudget, new: RunBudget) -> RunBudget:
    """Budget snapshots are monotonic (the tracker only accumulates), so
    concurrent stamps merge by keeping the most-used snapshot."""
    return new if new.tokens_used >= existing.tokens_used else existing


def keep_first_error(existing: str | None, new: str | None) -> str | None:
    """First error wins; concurrent branch failures don't clobber it."""
    return existing if existing is not None else new


# --- Structured-output schemas (what the LLM nodes must emit) ---


class DecomposedSubQuestion(BaseModel):
    """One sub-question as proposed by the decomposer."""

    text: str
    rationale: str = ""


class DecomposeOutput(BaseModel):
    """Decomposer output: the independent sub-questions of the analyst
    question. Empty or single-item means the question is single-part and
    passes through unchanged (no ceremony)."""

    sub_questions: list[DecomposedSubQuestion] = Field(default_factory=list)


class QueryRewrite(BaseModel):
    """A retrieval query restated in filing vocabulary (ADR 0022)."""

    query: str


class DraftClaim(BaseModel):
    """One claim as tagged by the synthesizer, before verification."""

    text: str
    claim_type: ClaimType
    source_chunk_ids: list[str] = Field(default_factory=list)
    # The sentence of `answer` that states this claim, quoted verbatim. The
    # synthesizer wrote both and knows the correspondence exactly; discarding
    # it forced `retract` to guess the mapping back by token overlap, which
    # deleted the wrong sentences (ADR 0023). Empty falls back to that guess.
    answer_span: str = ""


class SynthesisOutput(BaseModel):
    """Synthesizer output: the draft answer plus its decomposed claims."""

    answer: str
    # Required, deliberately without a default: a default drops `claims` from
    # the tool schema's `required`, a GLM backend then omits the key (50 of 84
    # drafts on 2026-10-09/10), and the omission parsed silently as `[]`.
    # Required, it is a validation error and `ainvoke_with_retry` re-asks; an
    # explicit `[]` still parses and still meets ADR 0038 (ADR 0046).
    claims: list[DraftClaim]


class JudgeOutput(BaseModel):
    """A verifier judge's structured ruling on one claim."""

    verdict: VerdictLabel
    supporting_span: str | None = None
    feedback: str | None = None


class CriticOutput(BaseModel):
    """The critic's rubric check on the full draft. `re_decompose` signals
    that the answer failed because the question was mis-split (not
    mis-researched) and the run should re-enter the decomposer."""

    passed: bool
    feedback: str | None = None
    re_decompose: bool = False


class EngineState(BaseModel):
    """The LangGraph state schema for one run of the engine."""

    question: str
    evidence: Annotated[list[EvidenceChunk], merge_evidence] = Field(default_factory=list)
    draft_answer: str | None = None
    run_budget: Annotated[RunBudget, merge_budgets] = Field(default_factory=RunBudget)

    sub_questions: list[SubQuestion] = Field(default_factory=list)
    branch_results: Annotated[list[BranchResult], operator.add] = Field(default_factory=list)
    decompose_iteration: int = 0
    redecompose_requested: bool = False

    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[Verdict] = Field(default_factory=list)
    verify_iteration: int = 0
    rejection_feedback: list[str] = Field(default_factory=list)

    critic_iteration: int = 0
    critic_passed: bool | None = None
    critic_feedback: str | None = None

    # Set by guardrails on budget/timeout aborts; downstream nodes short-
    # circuit and the conditional edges route to END with a partial result.
    error: Annotated[str | None, keep_first_error] = None


# --- Run result: the output contract (rendered by Phase 4 API / Phase 5 UI) ---


class SourceRef(BaseModel):
    """Citation metadata for one cited evidence chunk.

    `ticker` and `form_type` are here for the Phase 5 hover popover, which
    names the company and the form a claim rests on: "AAPL 10-K" is the
    difference between a citation a reader can place and an accession number
    they cannot.
    """

    chunk_id: str
    doc_id: str = ""
    ticker: str = ""
    form_type: str = ""
    section: str = ""
    accession_no: str = ""
    filing_date: str = ""


class GroundingRow(BaseModel):
    """One row of the grounding table: a claim with its verdict and sources."""

    claim_id: str
    text: str
    claim_type: ClaimType
    status: ClaimStatus
    verdict: VerdictLabel | None = None
    method: VerifyMethod | None = None
    supporting_span: str | None = None
    # The sentence of the *answer* this claim was drawn from — not to be
    # confused with `supporting_span` above, which quotes the *source*. Carried
    # into the payload because nothing else locates a claim in the prose, and
    # the Phase 5 grounding view is built on colouring the sentence a claim
    # came from. A quoted span survives the retraction edits that character
    # offsets would not (ADR 0023).
    answer_span: str = ""
    feedback: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class RunResult(BaseModel):
    """Answer + grounding table: the full result of one engine run."""

    run_id: str
    question: str
    answer: str
    sub_questions: list[SubQuestion] = Field(default_factory=list)
    grounding: list[GroundingRow] = Field(default_factory=list)
    retracted: list[GroundingRow] = Field(default_factory=list)
    verify_iterations: int = 0
    critic_iterations: int = 0
    critic_passed: bool | None = None
    budget: RunBudget = Field(default_factory=RunBudget)
    error: str | None = None
    # True when the run stopped at the human-in-the-loop gate instead of
    # finishing: the graph is checkpointed inside `review` and resumes once
    # the decisions arrive. The claims awaiting a decision are the grounding
    # rows whose status is in `UNRESOLVED_STATUSES` — no second list, so the
    # two can never disagree.
    awaiting_review: bool = False


def build_run_result(
    run_id: str, state: EngineState, *, awaiting_review: bool = False
) -> RunResult:
    """Derive the output contract from the final graph state (pure code)."""
    chunk_map = {c.chunk_id: c for c in state.evidence}
    verdict_map = {v.claim_id: v for v in state.verdicts}

    grounding: list[GroundingRow] = []
    retracted: list[GroundingRow] = []
    for claim in state.claims:
        verdict = verdict_map.get(claim.id)
        sources: list[SourceRef] = []
        for cid in claim.source_chunk_ids:
            chunk = chunk_map.get(cid)
            if chunk is None:
                sources.append(SourceRef(chunk_id=cid))
            else:
                sources.append(
                    SourceRef(
                        chunk_id=cid,
                        doc_id=chunk.doc_id,
                        # Chunk fields, not `metadata` entries like the two
                        # below them — the asymmetry is in `EvidenceChunk`,
                        # not a mistake here.
                        ticker=chunk.ticker,
                        form_type=chunk.form_type,
                        section=chunk.section,
                        accession_no=chunk.metadata.get("accession_no", ""),
                        filing_date=chunk.metadata.get("filing_date", ""),
                    )
                )
        row = GroundingRow(
            claim_id=claim.id,
            text=claim.text,
            claim_type=claim.claim_type,
            status=claim.status,
            verdict=verdict.verdict if verdict else None,
            method=verdict.method if verdict else None,
            supporting_span=verdict.supporting_span if verdict else None,
            answer_span=claim.answer_span,
            feedback=verdict.feedback if verdict else None,
            sources=sources,
        )
        (retracted if claim.status == "retracted" else grounding).append(row)

    return RunResult(
        run_id=run_id,
        question=state.question,
        answer=state.draft_answer or "",
        sub_questions=state.sub_questions,
        grounding=grounding,
        retracted=retracted,
        verify_iterations=state.verify_iteration,
        critic_iterations=state.critic_iteration,
        critic_passed=state.critic_passed,
        budget=state.run_budget,
        error=state.error,
        awaiting_review=awaiting_review,
    )
