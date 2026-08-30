"""CLI surface: argument wiring, output formatting, error exit codes.

The engine is faked — these assert how `fie ask` / `fie resume` shape and
print what the runner returns, not the answering itself.
"""

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from fie.cli import app
from fie.state import RunResult
from tests.fakes import make_result

runner = CliRunner()


def _patch_engine(monkeypatch: pytest.MonkeyPatch, result: RunResult) -> list[dict[str, Any]]:
    """Replace the engine with a fake that records how it was called."""
    calls: list[dict[str, Any]] = []

    async def fake(**kwargs: Any) -> RunResult:
        calls.append(kwargs)
        return result

    monkeypatch.setattr("fie.cli.answer_question", fake)
    return calls


def test_ask_prints_the_answer_and_grounding_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch, make_result(answer="Net sales were $391.0 billion [c1]."))
    out = runner.invoke(app, ["ask", "What were Apple's net sales?"])
    assert out.exit_code == 0
    assert "=== Answer ===" in out.stdout
    assert "$391.0 billion" in out.stdout
    assert "Grounding table" in out.stdout
    assert "Budget:" in out.stdout


def test_ask_json_emits_the_run_result_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_engine(monkeypatch, make_result(run_id="job-json"))
    out = runner.invoke(app, ["ask", "q", "--json"])
    assert out.exit_code == 0
    # The command prints a "Run: ..." line before the JSON payload.
    payload = json.loads(out.stdout[out.stdout.index("{") :])
    assert payload["run_id"] == "job-json"


def test_ask_reports_a_runtime_error_and_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**kwargs: Any) -> RunResult:
        raise RuntimeError("Qdrant is unreachable")

    monkeypatch.setattr("fie.cli.answer_question", boom)
    out = runner.invoke(app, ["ask", "q"])
    assert out.exit_code == 1
    assert "Qdrant is unreachable" in out.stdout


def test_resume_passes_question_none_and_the_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_engine(monkeypatch, make_result())
    out = runner.invoke(app, ["resume", "run-xyz"])
    assert out.exit_code == 0
    assert calls[0]["question"] is None
    assert calls[0]["run_id"] == "run-xyz"
