"""The human-in-the-loop gate (Phase 4, ADR 0034).

A run whose verify loop could not resolve every claim stops for a reviewer,
and the reviewer's decisions decide what reaches the reader. The load-bearing
assertion is the last one in `test_accepting_a_claim_keeps_it_in_the_prose`:
an accepted claim survives the prose but is *not* laundered into
`grounding_rate` — its verdict stays whatever the verifier ruled.
"""

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from fie.eval.metrics import grounding_rate, hallucination_rate
from fie.graph import GraphLimits
from fie.state import (
    RETRACTED_HEADER,
    CriticOutput,
    DraftClaim,
    EngineState,
    JudgeOutput,
    ReviewMode,
    SynthesisOutput,
    build_run_result,
)
from tests.fakes import StructuredFakeModel, StubRetriever, make_chunks, make_graph

QUESTION = "What were Apple's total net sales?"
NUMERIC_SOURCE = "(In millions) Total net sales were $ 391,035 for fiscal 2024, up 2%."
QUAL_SOURCE = "Services revenue grew due to advertising, cloud services and the App Store."

TRUE_NUMERIC = "Apple reported total net sales of $391.0 billion"
LIE_QUAL = "Growth was driven entirely by Vision Pro demand"

TRUE_SPAN = f"{TRUE_NUMERIC} [c1]."
LIE_SPAN = f"{LIE_QUAL} [c2]."


def _synthesis(*, with_lie: bool) -> SynthesisOutput:
    claims = [
        DraftClaim(
            text=TRUE_NUMERIC,
            claim_type="numeric",
            source_chunk_ids=["c1"],
            answer_span=TRUE_SPAN,
        )
    ]
    answer = TRUE_SPAN
    if with_lie:
        claims.append(
            DraftClaim(
                text=LIE_QUAL,
                claim_type="qualitative",
                source_chunk_ids=["c2"],
                answer_span=LIE_SPAN,
            )
        )
        answer = f"{TRUE_SPAN} {LIE_SPAN}"
    return SynthesisOutput(answer=answer, claims=claims)


def _graph(
    saver: Any, review_mode: ReviewMode, *, with_lie: bool = True
) -> CompiledStateGraph[EngineState]:
    return make_graph(
        retriever=StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE})),
        synthesis=StructuredFakeModel(outputs=[_synthesis(with_lie=with_lie)]),
        qualitative=StructuredFakeModel(
            default_output=JudgeOutput(verdict="unsupported", feedback="not in source")
        ),
        critic=StructuredFakeModel(default_output=CriticOutput(passed=True)),
        # One verify pass: the gate is only reached once the loop-back budget
        # is spent, so capping it at 1 puts the run at the gate immediately.
        limits=GraphLimits(
            max_verify_iterations=1, retry_attempts=1, retry_base_delay_seconds=0.01
        ),
        review_mode=review_mode,
        checkpointer=saver,
    )


def _config(thread: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread}}


async def test_gate_off_runs_straight_to_retraction(tmp_path: Path) -> None:
    """The default, and what the eval runs: no pause, no new behaviour."""
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph = _graph(saver, "off")
        config = _config("run-gate-off")
        raw = await graph.ainvoke(EngineState(question=QUESTION), config)
        snapshot = await graph.aget_state(config)

    assert snapshot.next == ()
    state = EngineState.model_validate(raw)
    assert {c.text: c.status for c in state.claims}[LIE_QUAL] == "retracted"


async def test_unresolved_claim_stops_the_run_for_review(tmp_path: Path) -> None:
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph = _graph(saver, "unresolved")
        config = _config("run-gate-pause")
        raw = await graph.ainvoke(EngineState(question=QUESTION), config)
        snapshot = await graph.aget_state(config)

    assert snapshot.next == ("review",)
    # The interrupt payload names exactly the claims a human has to rule on.
    (interrupt,) = raw["__interrupt__"]
    assert [c["text"] for c in interrupt.value["unresolved"]] == [LIE_QUAL]

    # The API reports this as `awaiting_review`, and the draft is the
    # pre-retraction one — nothing has been excised yet.
    result = build_run_result(
        "run-gate-pause", EngineState.model_validate(raw), awaiting_review=True
    )
    assert result.awaiting_review is True
    assert RETRACTED_HEADER not in result.answer
    assert LIE_QUAL in result.answer


async def test_accepting_a_claim_keeps_it_in_the_prose(tmp_path: Path) -> None:
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph = _graph(saver, "unresolved")
        config = _config("run-gate-accept")
        raw = await graph.ainvoke(EngineState(question=QUESTION), config)
        (interrupt,) = raw["__interrupt__"]
        claim_id = interrupt.value["unresolved"][0]["claim_id"]

        raw = await graph.ainvoke(
            Command(resume=[{"claim_id": claim_id, "action": "accept"}]), config
        )
        snapshot = await graph.aget_state(config)

    assert snapshot.next == ()
    state = EngineState.model_validate(raw)
    assert {c.text: c.status for c in state.claims}[LIE_QUAL] == "accepted"

    body, _, retracted_section = (state.draft_answer or "").partition(RETRACTED_HEADER)
    assert LIE_QUAL in body
    assert LIE_QUAL not in retracted_section

    # A reviewer waving a claim through is not the machine grounding it. The
    # row stays in `grounding` carrying the verifier's own `unsupported`
    # verdict, so the metrics still see an unsupported claim in the delivered
    # answer — the gate cannot launder one into `grounding_rate`.
    result = build_run_result("run-gate-accept", state)
    accepted = next(row for row in result.grounding if row.text == LIE_QUAL)
    assert accepted.status == "accepted"
    assert accepted.verdict == "unsupported"
    assert grounding_rate([result]) == 0.5
    assert hallucination_rate([result]) == 0.5


async def test_retracting_at_the_gate_excises_the_claim(tmp_path: Path) -> None:
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph = _graph(saver, "unresolved")
        config = _config("run-gate-retract")
        raw = await graph.ainvoke(EngineState(question=QUESTION), config)
        (interrupt,) = raw["__interrupt__"]
        claim_id = interrupt.value["unresolved"][0]["claim_id"]

        raw = await graph.ainvoke(
            Command(resume=[{"claim_id": claim_id, "action": "retract"}]), config
        )

    state = EngineState.model_validate(raw)
    assert {c.text: c.status for c in state.claims}[LIE_QUAL] == "retracted"
    body, _, retracted_section = (state.draft_answer or "").partition(RETRACTED_HEADER)
    assert LIE_QUAL not in body
    assert LIE_QUAL in retracted_section


async def test_no_decision_leaves_the_machine_outcome_standing(tmp_path: Path) -> None:
    """Silence is not consent: an unruled claim keeps the status the verifier
    gave it, so an empty review resolves exactly as a gate-less run would."""
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph = _graph(saver, "unresolved")
        config = _config("run-gate-silent")
        await graph.ainvoke(EngineState(question=QUESTION), config)
        raw = await graph.ainvoke(Command(resume=[]), config)

    state = EngineState.model_validate(raw)
    assert {c.text: c.status for c in state.claims}[LIE_QUAL] == "retracted"


async def test_always_mode_stops_a_run_that_resolved_cleanly(tmp_path: Path) -> None:
    """The demo flag: a clean run has nothing to rule on but still stops, so
    the gate can be shown on a question that happens to verify."""
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as saver:
        graph = _graph(saver, "always", with_lie=False)
        config = _config("run-gate-always")
        raw = await graph.ainvoke(EngineState(question=QUESTION), config)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("review",)
        (interrupt,) = raw["__interrupt__"]
        assert interrupt.value["unresolved"] == []

        raw = await graph.ainvoke(Command(resume=[]), config)

    state = EngineState.model_validate(raw)
    assert [c.status for c in state.claims] == ["grounded"]
    assert RETRACTED_HEADER not in (state.draft_answer or "")
