"""`fie export-runs` — the recorded demo surface (abuse-control layer 2).

The contract assertion is `test_an_exported_run_validates_as_the_live_result_payload`:
Phase 5 renders a live run and a recorded one through one Pydantic model, so
the export has to produce exactly what `GET /v1/jobs/{id}/result` returns. The
runs here are written by the *real* graph over fakes, so the export is read
back out of a checkpoint the engine actually wrote.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from fie.api.models import RunPayload
from fie.export import RecordedIndex, accepted_run_ids, export_runs
from fie.graph import GraphLimits
from fie.state import (
    CriticOutput,
    DraftClaim,
    EngineState,
    JudgeOutput,
    ReviewMode,
    SynthesisOutput,
)
from tests.fakes import StructuredFakeModel, StubRetriever, make_chunks, make_graph

QUESTION = "What were Apple's total net sales?"
SOURCE = "(In millions) Total net sales were $ 391,035 for fiscal 2024, up 2%."
CLAIM = "Apple reported total net sales of $391.0 billion"
SPAN = f"{CLAIM} [c1]."


def _graph(saver: Any, review_mode: ReviewMode = "off") -> CompiledStateGraph[EngineState]:
    return make_graph(
        retriever=StubRetriever(make_chunks({"c1": SOURCE})),
        synthesis=StructuredFakeModel(
            outputs=[
                SynthesisOutput(
                    answer=SPAN,
                    claims=[
                        DraftClaim(
                            text=CLAIM,
                            claim_type="qualitative",
                            source_chunk_ids=["c1"],
                            answer_span=SPAN,
                        )
                    ],
                )
            ]
        ),
        qualitative=StructuredFakeModel(
            default_output=JudgeOutput(verdict="unsupported", feedback="not in source")
        ),
        critic=StructuredFakeModel(default_output=CriticOutput(passed=True)),
        limits=GraphLimits(
            max_verify_iterations=1, retry_attempts=1, retry_base_delay_seconds=0.01
        ),
        review_mode=review_mode,
        checkpointer=saver,
    )


async def _record(checkpoint: Path, run_id: str, review_mode: ReviewMode = "off") -> None:
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        config: RunnableConfig = {"configurable": {"thread_id": run_id}}
        await _graph(saver, review_mode).ainvoke(EngineState(question=QUESTION), config)


async def test_an_exported_run_validates_as_the_live_result_payload(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoints.db"
    await _record(checkpoint, "run-a")

    report = export_runs(checkpoint_path=checkpoint, out_dir=tmp_path / "out", run_ids=["run-a"])
    assert len(report.written) == 1

    payload = RunPayload.model_validate_json((tmp_path / "out" / "runs" / "run-a.json").read_text())
    assert payload.job_id == "run-a"
    assert payload.source == "recorded"
    assert payload.created_at
    assert payload.result.question == QUESTION
    # The retraction is the point of a recorded run: the claim the verifier
    # could not support is shown as pulled out of the answer, not hidden.
    assert [row.status for row in payload.result.retracted] == ["retracted"]


async def test_the_index_lists_every_run_a_static_host_cannot(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoints.db"
    await _record(checkpoint, "run-a")
    await _record(checkpoint, "run-b")

    out = tmp_path / "out"
    export_runs(checkpoint_path=checkpoint, out_dir=out, run_ids=["run-a", "run-b"])

    index = RecordedIndex.model_validate_json((out / "index.json").read_text())
    assert {entry.job_id for entry in index.runs} == {"run-a", "run-b"}
    for entry in index.runs:
        # The path is relative to the index, so the frontend fetches without
        # reconstructing a layout.
        assert (out / entry.path).exists()
        assert entry.retracted == 1
        assert entry.question == QUESTION


async def test_a_run_parked_at_the_review_gate_is_not_exported(tmp_path: Path) -> None:
    """It has prose but not a settled grounding table. Shipping it would
    advertise a verdict the graph never reached."""
    checkpoint = tmp_path / "checkpoints.db"
    await _record(checkpoint, "run-parked", review_mode="unresolved")

    report = export_runs(
        checkpoint_path=checkpoint, out_dir=tmp_path / "out", run_ids=["run-parked"]
    )
    assert report.written == []
    assert report.skipped == [("run-parked", "run is unfinished (pending tasks in the checkpoint)")]


async def test_a_run_with_no_claims_is_not_exported(tmp_path: Path) -> None:
    """A claimless answer passes every gate the eval has — zero claims adds
    nothing to either side of `grounding_rate`'s ratio, so the run is not
    scored badly, it is not scored at all (ADR 0038). On the demo surface it
    renders as a stated figure above an empty grounding table, which inverts
    the thing being demonstrated, so it never becomes a file."""
    checkpoint = tmp_path / "checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        graph = make_graph(
            retriever=StubRetriever(make_chunks({"c1": SOURCE})),
            synthesis=StructuredFakeModel(outputs=[SynthesisOutput(answer=SPAN, claims=[])]),
            critic=StructuredFakeModel(default_output=CriticOutput(passed=True)),
            limits=GraphLimits(
                max_verify_iterations=1, retry_attempts=1, retry_base_delay_seconds=0.01
            ),
            checkpointer=saver,
        )
        config: RunnableConfig = {"configurable": {"thread_id": "run-empty"}}
        await graph.ainvoke(EngineState(question=QUESTION), config)

    report = export_runs(
        checkpoint_path=checkpoint, out_dir=tmp_path / "out", run_ids=["run-empty"]
    )
    assert report.written == []
    assert report.skipped == [("run-empty", "no claims (nothing to ground — ADR 0038)")]


def test_a_run_with_no_checkpoint_is_reported_not_guessed_at(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoints.db"
    checkpoint.touch()
    report = export_runs(checkpoint_path=checkpoint, out_dir=tmp_path / "out", run_ids=["ghost"])
    assert report.skipped == [("ghost", "no checkpoint")]
    assert (
        RecordedIndex.model_validate_json((tmp_path / "out" / "index.json").read_text()).runs == []
    )


def test_a_missing_checkpoint_store_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="does not exist"):
        export_runs(checkpoint_path=tmp_path / "nope.db", out_dir=tmp_path / "out", run_ids=[])


def test_the_default_selection_is_the_accepted_baselines_run(tmp_path: Path) -> None:
    """Not the newest report: the recorded surface shows the runs whose
    metrics are the published ones."""
    (tmp_path / "reports").mkdir()
    (tmp_path / "baselines.json").write_text(json.dumps({"accepted": "20260801-122527"}))
    (tmp_path / "reports" / "agent-20260801-122527.json").write_text(
        json.dumps({"run_ids": {"g-001": "abc", "g-002": "def"}})
    )
    (tmp_path / "reports" / "agent-20260802-000000.json").write_text(
        json.dumps({"run_ids": {"g-001": "newer"}})
    )
    assert accepted_run_ids(tmp_path) == {"g-001": "abc", "g-002": "def"}


def test_a_baseline_with_no_accepted_run_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "baselines.json").write_text(json.dumps({"metrics": {}}))
    with pytest.raises(RuntimeError, match="records no accepted run"):
        accepted_run_ids(tmp_path)
