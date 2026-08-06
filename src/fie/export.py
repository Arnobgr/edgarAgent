"""`fie export-runs` — the recorded demo surface.

Reads finished runs out of `data/checkpoints.db` and writes each one as the
exact payload `GET /v1/jobs/{id}/result` returns, so Phase 5 renders a live
run and a recorded one through one type. The files ship *inside* the
Cloudflare Pages bundle and are never served from the VPS: the visitor who
only reads is answered by the CDN and the box never sees the request. That is
abuse-control layer 2 of the Phase 4 plan, and it removes more load than
every rate limit below it combined.

Selection defaults to the run ids of the accepted eval baseline rather than
"every thread in the file". The checkpoint DB holds several hundred
development runs against thirty that a gate accepted, and only the latter are
worth showing a stranger.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field

from fie.api.models import RunPayload
from fie.state import EngineState, build_run_result

logger = logging.getLogger(__name__)


class RecordedRun(BaseModel):
    """One entry in the index: enough to render the picker without fetching
    every payload. `retracted` is here on purpose — a recorded run that shows
    a claim being pulled out of the answer is the best single demonstration
    this system has, so the frontend must be able to find one."""

    job_id: str
    question: str
    created_at: str
    path: str
    claims: int
    retracted: int


class RecordedIndex(BaseModel):
    """What a static host cannot provide: a directory listing.

    Lives here rather than in `fie.api.models` because nothing serves it over
    HTTP — it is the shape of the export, defined where the export is made.
    Carries no generation timestamp deliberately: the files are committed so
    the Pages build can bundle them, and a clock in the index would produce a
    diff on every export even when no run changed.
    """

    runs: list[RecordedRun] = Field(default_factory=list)


@dataclass(frozen=True)
class ExportReport:
    written: list[Path] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    pruned: list[Path] = field(default_factory=list)

    def lines(self) -> Iterator[str]:
        yield f"wrote {len(self.written)} run(s)"
        for run_id, why in self.skipped:
            yield f"  skipped {run_id}: {why}"
        for path in self.pruned:
            yield f"  removed {path.name}: no longer in the export"


def accepted_run_ids(eval_dir: Path) -> dict[str, str]:
    """`{gold question id: run id}` for the eval run `baselines.json` accepted.

    Going through the baseline rather than the newest report is the point:
    the recorded surface then shows exactly the run whose metrics are
    published, so a visitor reading the numbers in the README is looking at
    the runs that produced them.
    """
    baselines_path = eval_dir / "baselines.json"
    baselines = json.loads(baselines_path.read_text())
    accepted = baselines.get("accepted")
    if not accepted:
        raise RuntimeError(f"{baselines_path} records no accepted run")
    report_path = eval_dir / "reports" / f"agent-{accepted}.json"
    if not report_path.exists():
        raise RuntimeError(f"accepted run {accepted} has no report at {report_path}")
    run_ids: dict[str, str] = json.loads(report_path.read_text()).get("run_ids", {})
    if not run_ids:
        raise RuntimeError(f"{report_path} lists no run ids")
    return run_ids


def read_run(saver: SqliteSaver, run_id: str) -> tuple[RunPayload | None, str]:
    """The finished payload for one run, or a reason it cannot be exported.

    The latest checkpoint of a thread is its final state, so the same pure
    `build_run_result` the live path uses derives the contract — no second
    rendering of the grounding table that could drift from the first.
    """
    tuple_ = saver.get_tuple({"configurable": {"thread_id": run_id}})
    if tuple_ is None:
        return None, "no checkpoint"
    channels: dict[str, Any] = tuple_.checkpoint.get("channel_values", {})
    result = build_run_result(run_id, EngineState.model_validate(channels))
    if result.error:
        return None, f"run ended in error: {result.error}"
    if not result.answer:
        return None, "no answer (run did not reach synthesis)"
    if not result.grounding and not result.retracted:
        # Run `2b11fa1d4f9e` of the accepted baseline states a specific dollar
        # figure in a 434-character draft carrying zero claims, so it renders
        # as an answer above an empty grounding table — the inverse of what
        # this surface exists to demonstrate. ADR 0038 stops such a run being
        # *created*; this stops one being *shown*, which is where the damage
        # is visible. Skipped rather than filtered at the call site so the
        # exclusion is announced on every export instead of living in a
        # `--run-ids` invocation nobody kept.
        return None, "no claims (nothing to ground — ADR 0038)"
    if tuple_.pending_writes or channels.get("__pregel_tasks"):
        # A thread parked mid-flight — a killed run, or one still waiting at
        # the review gate (whose interrupt is stored as a pending write). It
        # has prose but not a settled grounding table, and shipping it would
        # advertise a verdict the graph never reached.
        return None, "run is unfinished (pending tasks in the checkpoint)"
    return (
        RunPayload(
            job_id=run_id,
            source="recorded",
            # The final checkpoint's timestamp: when the run *finished*. The
            # live path fills this from the job row's submission time; for a
            # recorded run the two differ by the run's own duration and only
            # the finish is recoverable from a checkpoint.
            created_at=str(tuple_.checkpoint.get("ts", "")),
            result=result,
        ),
        "",
    )


def export_runs(
    *, checkpoint_path: Path, out_dir: Path, run_ids: list[str], prune: bool = False
) -> ExportReport:
    """Write `<out>/runs/<run_id>.json` per run plus `<out>/index.json`.

    `prune` removes run files this export did not write. It matters at the
    *second* export and not the first: run ids are per-run, so accepting a new
    baseline changes all of them, and without pruning the previous baseline's
    files stay on disk as a second set of runs that `index.json` no longer
    lists but the bundle still ships. Off by default, and the caller turns it
    on only for a full export — pruning a `--run-ids` subset would delete
    every run the subset left out.
    """
    if not checkpoint_path.exists():
        raise RuntimeError(f"checkpoint store {checkpoint_path} does not exist — no runs to export")
    runs_dir = out_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped: list[tuple[str, str]] = []
    entries: list[RecordedRun] = []
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        for run_id in run_ids:
            payload, why = read_run(saver, run_id)
            if payload is None:
                skipped.append((run_id, why))
                logger.info("skipping %s: %s", run_id, why)
                continue
            path = runs_dir / f"{run_id}.json"
            path.write_text(payload.model_dump_json(indent=2) + "\n")
            written.append(path)
            entries.append(
                RecordedRun(
                    job_id=run_id,
                    question=payload.result.question,
                    created_at=payload.created_at,
                    path=f"runs/{run_id}.json",
                    claims=len(payload.result.grounding) + len(payload.result.retracted),
                    retracted=len(payload.result.retracted),
                )
            )

    pruned: list[Path] = []
    if prune and written:
        # Guarded on `written` as well as `prune`: an export that produced
        # nothing is a failed export, and emptying the demo surface is the
        # worst possible response to one.
        keep = {path.name for path in written}
        pruned = sorted(p for p in runs_dir.glob("*.json") if p.name not in keep)
        for path in pruned:
            path.unlink()
            logger.info("pruned %s", path.name)

    entries.sort(key=lambda e: (e.created_at, e.job_id))
    index = RecordedIndex(runs=entries)
    (out_dir / "index.json").write_text(index.model_dump_json(indent=2) + "\n")
    return ExportReport(written=written, skipped=skipped, pruned=pruned)
