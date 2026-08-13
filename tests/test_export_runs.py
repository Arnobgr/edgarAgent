"""`fie export-runs` — the recorded demo surface (abuse-control layer 2).

The contract assertion is `test_an_exported_run_validates_as_the_live_result_payload`:
Phase 5 renders a live run and a recorded one through one Pydantic model, so
the export has to produce exactly what `GET /v1/jobs/{id}/result` returns. The
runs here are written by the *real* graph over fakes, so the export is read
back out of a checkpoint the engine actually wrote.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from fie.api.models import RunPayload
from fie.export import RecordedIndex, accepted_run_ids, export_runs, read_run
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

# The checked-out repository, not a fixture: the assertion at the foot of this
# file is about the files that actually ship.
REPO_ROOT = Path(__file__).resolve().parents[1]

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


class StoredRun:
    """A checkpoint handed straight to `read_run`, which takes its saver as an
    argument. Every other run here is written by the real graph; this one
    cannot be, because ADR 0038 §1 now fails a claimless synthesis at the node
    — and that is exactly why the export still has to refuse one. The 30 runs
    of the accepted baseline were checkpointed before §1 existed, `recorded/`
    is regenerated from them, and one of them claims nothing."""

    def __init__(self, state: EngineState) -> None:
        self.state = state

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple:
        return CheckpointTuple(
            config=config,
            checkpoint={"channel_values": self.state.model_dump(), "ts": "2026-08-01T12:25:27"},  # type: ignore[typeddict-item]
            metadata=None,
        )


def test_a_run_with_no_claims_is_not_exported() -> None:
    """A claimless answer passes every gate the eval has — zero claims adds
    nothing to either side of `grounding_rate`'s ratio, so the run is not
    scored badly, it is not scored at all (ADR 0038). On the demo surface it
    renders as a stated figure above an empty grounding table, which inverts
    the thing being demonstrated, so it never becomes a file."""
    stored = StoredRun(EngineState(question=QUESTION, draft_answer=SPAN))
    payload, why = read_run(stored, "2b11fa1d4f9e")  # type: ignore[arg-type]
    assert payload is None
    assert why == "no claims (nothing to ground — ADR 0038)"


async def test_a_full_export_removes_runs_it_no_longer_writes(tmp_path: Path) -> None:
    """The second export is where this matters. Run ids are per-run, so
    accepting a new baseline changes all thirty; without pruning the previous
    baseline's files stay on disk as a second set of runs `index.json` does
    not list but the Pages bundle still ships."""
    checkpoint = tmp_path / "checkpoints.db"
    await _record(checkpoint, "run-a")
    out = tmp_path / "out"
    stale = out / "runs" / "run-from-the-last-baseline.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")

    report = export_runs(checkpoint_path=checkpoint, out_dir=out, run_ids=["run-a"], prune=True)

    assert [p.name for p in report.pruned] == ["run-from-the-last-baseline.json"]
    assert not stale.exists()
    assert (out / "runs" / "run-a.json").exists()


async def test_a_partial_export_leaves_the_runs_it_was_not_asked_about(tmp_path: Path) -> None:
    """`--run-ids` names a subset, so it owns a subset. Pruning there would
    delete every run the invocation left out."""
    checkpoint = tmp_path / "checkpoints.db"
    await _record(checkpoint, "run-a")
    out = tmp_path / "out"
    export_runs(checkpoint_path=checkpoint, out_dir=out, run_ids=["run-a"])
    other = out / "runs" / "run-b.json"
    other.write_text("{}")

    report = export_runs(checkpoint_path=checkpoint, out_dir=out, run_ids=["run-a"])

    assert report.pruned == []
    assert other.exists()


async def test_an_export_that_wrote_nothing_never_empties_the_surface(tmp_path: Path) -> None:
    """A failed export is the worst possible moment to delete the runs that
    are currently being served."""
    checkpoint = tmp_path / "checkpoints.db"
    await _record(checkpoint, "run-a")
    out = tmp_path / "out"
    export_runs(checkpoint_path=checkpoint, out_dir=out, run_ids=["run-a"], prune=True)

    report = export_runs(checkpoint_path=checkpoint, out_dir=out, run_ids=["ghost"], prune=True)

    assert report.written == []
    assert report.pruned == []
    assert (out / "runs" / "run-a.json").exists()


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


def test_the_landing_run_the_spa_hardcodes_is_actually_exported() -> None:
    """`recorded/` is regenerated whenever a new baseline is accepted, and
    every run id changes with it — so the id the SPA lands a stranger on can
    stop existing without one line of code changing.

    That happened on 2026-08-13 (ADR 0043) and nothing failed: Cloudflare
    Pages answers a missing asset with the SPA shell under HTTP 200, so the
    page fetched `index.html`, tried to parse it as a run payload, and broke
    in the browser. A status code proved nothing, which is the same trap
    `/healthz` is documented for.

    The frontend suite catches it too, and does not count: CI runs no vitest
    step and `make test` is pytest only, so this is the assertion that is
    actually executed before a deploy."""
    source = (REPO_ROOT / "frontend" / "src" / "lib" / "recorded.ts").read_text()
    match = re.search(r'LANDING_RUN_ID\s*=\s*"([0-9a-f]+)"', source)
    assert match, "recorded.ts no longer declares LANDING_RUN_ID the way this test reads it"
    landing = match.group(1)

    index = json.loads((REPO_ROOT / "recorded" / "index.json").read_text())
    exported = {run["job_id"] for run in index["runs"]}
    assert landing in exported, (
        f"LANDING_RUN_ID {landing} is not in recorded/index.json — re-point it after "
        "regenerating recorded/, or the landing page 200s onto the SPA shell"
    )
    assert (REPO_ROOT / "recorded" / "runs" / f"{landing}.json").exists()
