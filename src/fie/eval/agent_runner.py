"""`uv run eval-agent` — the end-to-end gold-set regression runner.

Runs the gold questions through the production graph (same wiring as
`fie ask`), computes the §6 metrics, writes a JSON + markdown report with
Langfuse-linked run ids, and gates against `eval/baselines.json`: any
metric below its accepted baseline exits non-zero. This is the CI gate in
Phase 4 and the phase-advance gate from Phase 3 on (blueprint §13).
"""

import asyncio
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import typer

from fie.config import Settings, get_settings
from fie.eval.datasets import (
    EVAL_DIR,
    GoldQuestion,
    LabeledClaim,
    load_jsonl,
)
from fie.eval.metrics import (
    CitationJudgment,
    citation_accuracy,
    grounding_rate,
    hallucination_rate,
    numeric_fidelity,
)
from fie.eval.verifier_eval import run_verifier_eval
from fie.llm import get_model
from fie.observability import new_run_id
from fie.runner import answer_question, build_production_reconciler
from fie.state import JudgeOutput, RunResult

logger = logging.getLogger(__name__)

app = typer.Typer()

# Metrics where lower is better; everything else gates on a drop.
_LOWER_IS_BETTER = {"hallucination_rate"}

# Exit-gate targets to beat before Phase 4 (plan §exit gate) — informational
# in the report; the hard gate is the accepted baseline.
TARGETS = {"hallucination_rate": 0.02, "numeric_fidelity": 0.95, "grounding_rate": 0.90}


def judge_citations(results: list[RunResult], settings: Settings) -> list[CitationJudgment]:
    """LLM-judge every grounded claim's supporting span (eval_judge role,
    claude-opus-4-8). The report marks a deterministic 20% sample for the
    hand spot-check."""
    judge = get_model("eval_judge", settings).with_structured_output(JudgeOutput)
    judgments: list[CitationJudgment] = []
    for result in results:
        for row in result.grounding:
            if row.verdict != "grounded" or not row.supporting_span:
                continue
            messages = [
                (
                    "system",
                    "Judge whether the SPAN, on its own, supports the CLAIM. "
                    "Answer grounded only if a careful analyst would accept the "
                    "span as sufficient evidence for every part of the claim.",
                ),
                ("human", f"CLAIM: {row.text}\n\nSPAN: {row.supporting_span}"),
            ]
            try:
                ruling = judge.invoke(messages)
                output = (
                    ruling
                    if isinstance(ruling, JudgeOutput)
                    else JudgeOutput.model_validate(ruling)
                )
                supported = output.verdict == "grounded"
            except Exception as exc:
                logger.warning("citation judge failed on %s: %s", row.claim_id, exc)
                supported = False
            judgments.append(CitationJudgment(claim_id=row.claim_id, supported=supported))
    return judgments


def expected_fact_coverage(
    results: dict[str, RunResult], questions: list[GoldQuestion], settings: Settings
) -> float:
    """Informational: fraction of expected XBRL facts (values pulled from
    the facts store) whose figure appears in the answer to their question."""
    from fie.ingest.store import IngestStore
    from fie.tools.reconcile import parse_numbers

    store = IngestStore(settings.ingest_db_path)
    try:
        total = 0
        covered = 0
        for question in questions:
            result = results.get(question.id)
            if result is None or not question.answerable:
                continue
            answer_values = [n.value for n in parse_numbers(result.answer)]
            for expected in question.expected_facts:
                if expected.kind != "xbrl":
                    continue
                facts = store.query_facts(
                    tickers=[expected.ticker] if expected.ticker else None,
                    concept_terms=expected.concept_terms or None,
                    fiscal_year=expected.fiscal_year,
                    fiscal_period=expected.fiscal_period,
                    unit=expected.unit,
                    include_dimensioned=False,
                    limit=5,
                )
                if not facts:
                    logger.warning("gold %s: no fact in store for %s", question.id, expected)
                    continue
                total += 1
                target = facts[0].value
                if any(abs(v - target) <= abs(target) * 0.005 + 0.5 for v in answer_values):
                    covered += 1
        return covered / total if total else 0.0
    finally:
        store.close()


def declined_unanswerables(results: dict[str, RunResult], questions: list[GoldQuestion]) -> float:
    """Fraction of unanswerable questions where no fabricated numeric claim
    survives (retraction/decline is the system working)."""
    unanswerable = [q for q in questions if not q.answerable]
    if not unanswerable:
        return 1.0
    ok = 0
    for question in unanswerable:
        result = results.get(question.id)
        if result is None:
            continue
        surviving_numeric = [
            row
            for row in result.grounding
            if row.claim_type == "numeric" and row.verdict != "grounded"
        ]
        if not surviving_numeric:
            ok += 1
    return ok / len(unanswerable)


async def _run_gold(
    questions: list[GoldQuestion], settings: Settings
) -> tuple[dict[str, RunResult], dict[str, str]]:
    results: dict[str, RunResult] = {}
    run_ids: dict[str, str] = {}
    for question in questions:
        run_id = new_run_id()
        run_ids[question.id] = run_id
        typer.echo(f"[{question.id}] {question.question}")
        try:
            results[question.id] = await answer_question(
                question=question.question,
                run_id=run_id,
                settings=settings,
                run_name="fie-eval-agent",
            )
        except Exception as exc:
            logger.error("gold run %s failed: %s", question.id, exc)
    return results, run_ids


def _verifier_metrics(settings: Settings, metrics: dict[str, Any]) -> None:
    labeled_path = EVAL_DIR / "verifier_claims.jsonl"
    if not labeled_path.exists():
        metrics["verifier_note"] = (
            "verifier_claims.jsonl missing — generate with `uv run eval-seed` after ingest"
        )
        return
    labeled = load_jsonl(labeled_path, LabeledClaim)
    reconciler = build_production_reconciler(settings)
    judge = get_model("verify_qualitative", settings).with_structured_output(JudgeOutput)
    _, scores = run_verifier_eval(labeled, reconciler=reconciler, qualitative_judge=judge)
    for score in scores:
        metrics[f"verifier_precision_{score.claim_type}"] = score.precision
        metrics[f"verifier_recall_{score.claim_type}"] = score.recall


def _gate(metrics: dict[str, Any], baselines: dict[str, float | None]) -> list[str]:
    failures = []
    for name, baseline in baselines.items():
        if baseline is None or name not in metrics or not isinstance(metrics[name], int | float):
            continue
        current = float(metrics[name])
        if name in _LOWER_IS_BETTER:
            if current > baseline:
                failures.append(f"{name}: {current:.3f} above baseline {baseline:.3f}")
        elif current < baseline:
            failures.append(f"{name}: {current:.3f} below baseline {baseline:.3f}")
    return failures


@app.command()
def run(
    gold: str = typer.Option("", "--gold", help="Gold-set JSONL path override."),
    baselines: str = typer.Option("", "--baselines", help="Baselines JSON path override."),
    write_baseline: bool = typer.Option(
        False, "--write-baseline", help="Accept this run's numbers as the new baseline."
    ),
    limit: int = typer.Option(0, "--limit", help="Run only the first N questions (smoke)."),
    skip_judge: bool = typer.Option(
        False, "--skip-judge", help="Skip the opus citation judge (cheaper smoke runs)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run the gold set end-to-end, compute §6 metrics, gate on baselines."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()
    gold_path = Path(gold) if gold else EVAL_DIR / "gold_set.jsonl"
    baselines_path = Path(baselines) if baselines else EVAL_DIR / "baselines.json"
    questions = load_jsonl(gold_path, GoldQuestion)
    if limit:
        questions = questions[:limit]

    results, run_ids = asyncio.run(_run_gold(questions, settings))
    result_list = list(results.values())

    metrics: dict[str, Any] = {
        "n_questions": len(questions),
        "n_completed": len(result_list),
        "grounding_rate": grounding_rate(result_list),
        "hallucination_rate": hallucination_rate(result_list),
        "unanswerable_declined": declined_unanswerables(results, questions),
        "expected_fact_coverage": expected_fact_coverage(results, questions, settings),
    }

    from fie.ingest.store import IngestStore

    store = IngestStore(settings.ingest_db_path)
    try:
        fact_values = [f.value for f in store.query_facts(limit=10**9)]
    finally:
        store.close()
    metrics["numeric_fidelity"] = numeric_fidelity(result_list, fact_values)

    if skip_judge:
        metrics["citation_accuracy"] = None
    else:
        judgments = judge_citations(result_list, settings)
        metrics["citation_accuracy"] = citation_accuracy(judgments)
        metrics["citation_judged"] = len(judgments)
        # Deterministic 20% hand spot-check sample.
        ordered = sorted(judgments, key=lambda x: x.claim_id)
        metrics["citation_spot_check_sample"] = [
            j.claim_id for i, j in enumerate(ordered) if i % 5 == 0
        ]

    _verifier_metrics(settings, metrics)

    report_dir = EVAL_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {"generated": stamp, "run_ids": run_ids, "metrics": metrics, "targets": TARGETS}
    report_path = report_dir / f"agent-{stamp}.json"
    report_path.write_text(json.dumps(payload, indent=2))

    typer.echo("\n=== eval-agent metrics ===")
    for key, value in metrics.items():
        typer.echo(f"{key}: {value:.3f}" if isinstance(value, float) else f"{key}: {value}")
    typer.echo(f"Report: {report_path}")

    if write_baseline:
        gated = {
            k: v
            for k, v in metrics.items()
            if isinstance(v, int | float)
            and not isinstance(v, bool)
            and k.startswith(
                ("grounding", "hallucination", "citation_accuracy", "numeric", "verifier")
            )
        }
        baselines_path.write_text(
            json.dumps({"accepted": stamp, "metrics": gated}, indent=2) + "\n"
        )
        typer.echo(f"Baseline written: {baselines_path}")
        return

    if baselines_path.exists():
        baseline_metrics = json.loads(baselines_path.read_text()).get("metrics", {})
        failures = _gate(metrics, baseline_metrics)
        if failures:
            for failure in failures:
                typer.secho(f"REGRESSION: {failure}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.echo("All gated metrics at or above baseline.")


def main() -> None:
    app()
