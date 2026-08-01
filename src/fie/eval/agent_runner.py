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
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

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
from fie.ingest.store import Fact
from fie.llm import bind_structured, get_model
from fie.observability import new_run_id
from fie.runner import answer_question, build_production_reconciler
from fie.state import JudgeOutput, RunResult
from fie.tools.reconcile import ParsedNumber, matches_at_stated_precision

logger = logging.getLogger(__name__)

app = typer.Typer()

# Metrics where lower is better; everything else gates on a drop.
_LOWER_IS_BETTER = {"hallucination_rate"}

# Exit-gate targets to beat before Phase 4 (plan §exit gate) — informational
# in the report; the hard gate is the accepted baseline.
#
# numeric_fidelity is 0.85, not the §13 draft's 0.95: once the metric was
# scoped to actual financial figures (ADR 0015) its honest ceiling on real
# answers is ~0.9, because derived arithmetic and correct-but-unfiled
# narrative figures score as misses by construction. Its role is a
# fabrication tripwire — a system inventing numbers scores ~0.5 (the measured
# chance floor) — so the target sits above that floor and below the ceiling.
#
# Every other entry measures the *quality of what was said*, so a system that
# answers all 30 questions with "I cannot determine that from the filings"
# scores a perfect gate: no claims means no ungrounded claims, no
# hallucinations, no numeric errors. `expected_fact_coverage` is the only
# entry that costs something when the system says nothing, which is why it is
# gated rather than informational (ADR 0020). Its threshold is set from the
# measured post-fix value, not from ambition — picking it in advance is how
# numeric_fidelity's 0.95 came to refuse a legitimate baseline (ADR 0015).
#
# 0.65 comes from one measurement: the 2026-07-31 subset run took its 14
# labels from 0.286 to 1.000, which puts a floor of 0.714 on the full set if
# the 14 unmeasured labels do not improve at all. The target sits below that
# floor — the unmeasured half is the harder cross-company questions — and far
# above both the pre-fix 0.357 and the 0.000 a declining system scores.
# Raise it once a full baseline has measured all 28.
TARGETS = {
    "hallucination_rate": 0.02,
    "numeric_fidelity": 0.85,
    "grounding_rate": 0.90,
    "expected_fact_coverage": 0.65,
}


def judge_citations(
    results: list[RunResult], settings: Settings, judge: Any = None
) -> tuple[list[CitationJudgment], int]:
    """LLM-judge every grounded claim's supporting span (eval_judge role;
    model per config). The report marks a deterministic 20% sample for the
    hand spot-check.

    Claim ids are qualified by run id — bare `claim-1-1` repeats across
    questions, which made the 2026-07-27 spot-check sample ambiguous.
    Judge *failures* are excluded and counted separately instead of scored
    as bad citations: an API hiccup is not a citation error (ADR 0015)."""
    if judge is None:
        judge = bind_structured(get_model("eval_judge", settings), JudgeOutput)
    judgments: list[CitationJudgment] = []
    failures = 0
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
            claim_id = f"{result.run_id}:{row.claim_id}"
            try:
                ruling = judge.invoke(messages)
                output = (
                    ruling
                    if isinstance(ruling, JudgeOutput)
                    else JudgeOutput.model_validate(ruling)
                )
            except Exception as exc:
                logger.warning("citation judge failed on %s: %s", claim_id, exc)
                failures += 1
                continue
            judgments.append(
                CitationJudgment(claim_id=claim_id, supported=output.verdict == "grounded")
            )
    return judgments, failures


CoverageBucket = Literal["filed", "derived", "absent", "unresolved"]


@dataclass(frozen=True)
class FactCoverage:
    """How one expected fact fared in the answer to its question."""

    question_id: str
    description: str
    value: float | None
    bucket: CoverageBucket


def _coverage_bucket(
    value: float, numbers: list[ParsedNumber], context_scale: float | None
) -> CoverageBucket:
    """Which of the three ways an answer can relate to a filed figure.

    `filed` reuses the reconciler's own grounding predicate, so "the same
    figure" means one thing across the system: $416 billion against a filed
    416,161 is the filed figure at coarser precision and counts, exactly as
    ADR 0012 admits it elsewhere. `derived` is a *different* number that
    lands near the filed one — g-003 computed $18,571M from "8.6% of
    revenue" against a filed 18,497 — and is recorded but never credited,
    because a metric that credits arithmetic goes blind to the one thing it
    exists to detect: the figure was in the corpus and we failed to surface
    it (ADR 0020)."""
    for number in numbers:
        for form in _scale_forms(number, context_scale):
            if matches_at_stated_precision(form, value):
                return "filed"
    for number in numbers:
        for form in _scale_forms(number, context_scale):
            if abs(form.value - value) <= abs(value) * 0.005 + 0.5:
                return "derived"
    return "absent"


def _scale_forms(number: ParsedNumber, context_scale: float | None) -> list[ParsedNumber]:
    """A figure as printed, plus its reading under the answer's own "(in
    millions)" header — the same context-scale rule the reconciler applies
    to source chunks, tolerance scaled alongside (ADR 0019)."""
    if context_scale is None or number.explicit_scale:
        return [number]
    return [
        number,
        replace(
            number,
            value=number.value * context_scale,
            tolerance=number.tolerance * context_scale,
        ),
    ]


def _resolve(facts: list[Fact], concept_terms: list[str]) -> Fact:
    """The one fact a label names.

    Concept terms match as substrings, so `us-gaap:Assets` also reaches
    `us-gaap:AssetsCurrent` — 4 of the 28 gold labels resolve correctly only
    because the store orders by concept and a headline total is a prefix of
    its own refinements. An exact concept match wins outright rather than
    leaving the right value to alphabetical luck."""
    wanted = {term.lower() for term in concept_terms}
    return next((fact for fact in facts if fact.concept.lower() in wanted), facts[0])


def expected_fact_coverage(
    results: dict[str, RunResult], questions: list[GoldQuestion], settings: Settings
) -> tuple[float, list[FactCoverage]]:
    """Recall: fraction of expected XBRL facts whose *filed* figure the
    answer delivered, plus the per-label breakdown.

    This is the only instrument that detects "the fact was in the corpus and
    the system failed to surface it", which is why it counts `filed` alone —
    the alternative reading ("the analyst got a correct number") is already
    measured by `numeric_fidelity` and the reconciler, and adopting it here
    would let a system that retrieves nothing score well by deriving figures
    from percentages it read in prose (ADR 0020).

    Every expected fact of an answerable question stays in the denominator:
    a fact that fails to resolve in the store, or whose question produced no
    result, scores as a miss instead of silently shrinking the metric — the
    2026-07-27 run dropped 5 of its 15 labels that way (ADR 0015). Each
    label now resolves to exactly one value: instants carry their balance
    date, so the metric no longer scores against an arbitrary 5-row sample
    that for NVDA's total assets never contained the year-end figure."""
    from fie.eval.metrics import delivered_prose
    from fie.ingest.store import IngestStore
    from fie.tools.reconcile import _context_scale, parse_numbers

    store = IngestStore(settings.ingest_db_path)
    rows: list[FactCoverage] = []
    try:
        for question in questions:
            if not question.answerable:
                continue
            result = results.get(question.id)
            prose = delivered_prose(result.answer) if result else ""
            # Percent figures are excluded for the same reason
            # `numeric_fidelity` excludes them: they are derived, not filed.
            numbers = [n for n in parse_numbers(prose) if not n.is_percent]
            context_scale = _context_scale(prose)
            for expected in question.expected_facts:
                if expected.kind != "xbrl":
                    continue
                facts = store.query_facts(
                    tickers=[expected.ticker] if expected.ticker else None,
                    concept_terms=expected.concept_terms or None,
                    fiscal_year=expected.fiscal_year,
                    fiscal_period=expected.fiscal_period,
                    period_end=expected.period_end,
                    unit=expected.unit,
                    include_dimensioned=False,
                    limit=5,
                )
                if not facts:
                    logger.warning(
                        "gold %s: no fact in store for %s — scored as a miss",
                        question.id,
                        expected,
                    )
                    rows.append(FactCoverage(question.id, expected.description, None, "unresolved"))
                    continue
                value = _resolve(facts, expected.concept_terms).value
                rows.append(
                    FactCoverage(
                        question.id,
                        expected.description,
                        value,
                        _coverage_bucket(value, numbers, context_scale),
                    )
                )
    finally:
        store.close()
    filed = sum(1 for row in rows if row.bucket == "filed")
    return (filed / len(rows) if rows else 0.0), rows


def declined_unanswerables(results: dict[str, RunResult], questions: list[GoldQuestion]) -> float:
    """Fraction of unanswerable questions that produced an answer in which no
    fabricated numeric claim survives (retraction/decline is the system
    working).

    An errored or empty result scores as a miss rather than a decline: it
    carries no claims, so it would otherwise sail through the
    no-surviving-numeric check and credit an abort as a refusal — the same
    pattern as the retraction bug (ADR 0015). Three of the 30 runs on
    2026-07-27 ended that way."""
    unanswerable = [q for q in questions if not q.answerable]
    if not unanswerable:
        return 1.0
    ok = 0
    for question in unanswerable:
        result = results.get(question.id)
        if result is None or result.error or not result.answer.strip():
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
) -> tuple[dict[str, RunResult], dict[str, str], int]:
    results: dict[str, RunResult] = {}
    run_ids: dict[str, str] = {}
    degraded_attempts = 0
    for question in questions:
        # One retry with a fresh run id: an infra death (the recurring,
        # still-unreproduced SQLITE_MISUSE killed one question in each of
        # the last two 30-Q runs) must not cost the run its baseline when
        # the guard demands 30/30. A question that fails twice still drops
        # loudly.
        #
        # A guardrail abort is the same class of infra failure but arrives
        # as a *return*: `guarded` turns a node timeout into `error` on the
        # state, the graph routes to END, and `answer_question` hands back a
        # degraded RunResult that no exception marks. Its claims then score.
        # Two synthesize calls stalled at the 600s cap on 2026-07-31 and that
        # alone decided the run — hallucination_rate 0.049 (8/163, target
        # 0.02) and expected_fact_coverage 0.750; without them 0.000 and
        # 0.857. Measured across the six 30-Q runs a synthesize call stalls
        # ~1.5% of the time, so a clean 30/30 is a ~1-in-3 event unretried.
        # Keep the result either way: dropping the question would trade a
        # visible bad number for an invisible one.
        for attempt in (1, 2):
            run_id = new_run_id()
            run_ids[question.id] = run_id
            typer.echo(f"[{question.id}] {question.question}")
            try:
                result = await answer_question(
                    question=question.question,
                    run_id=run_id,
                    settings=settings,
                    run_name="fie-eval-agent",
                )
            except Exception as exc:
                logger.error("gold run %s failed (attempt %d): %s", question.id, attempt, exc)
                continue
            results[question.id] = result
            if result.error is None:
                break
            degraded_attempts += 1
            logger.error(
                "gold run %s degraded (attempt %d, run %s): %s",
                question.id,
                attempt,
                run_id,
                result.error,
            )
    return results, run_ids, degraded_attempts


def _verifier_metrics(settings: Settings, metrics: dict[str, Any]) -> None:
    labeled_path = EVAL_DIR / "verifier_claims.jsonl"
    if not labeled_path.exists():
        metrics["verifier_note"] = (
            "verifier_claims.jsonl missing — generate with `uv run eval-seed` after ingest"
        )
        return
    labeled = load_jsonl(labeled_path, LabeledClaim)
    reconciler = build_production_reconciler(settings)
    judge = bind_structured(get_model("verify_qualitative", settings), JudgeOutput)
    try:
        _, scores = run_verifier_eval(labeled, reconciler=reconciler, qualitative_judge=judge)
    finally:
        reconciler.close()
    for score in scores:
        metrics[f"verifier_precision_{score.claim_type}"] = score.precision
        metrics[f"verifier_recall_{score.claim_type}"] = score.recall


def _gate(metrics: dict[str, Any], baselines: Mapping[str, float | None]) -> list[str]:
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
    force: bool = typer.Option(
        False,
        "--force",
        help="Write the baseline even when the run misses the §13 targets or dropped questions.",
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

    results, run_ids, degraded_attempts = asyncio.run(_run_gold(questions, settings))
    result_list = list(results.values())

    coverage, coverage_rows = expected_fact_coverage(results, questions, settings)
    metrics: dict[str, Any] = {
        "n_questions": len(questions),
        "n_completed": len(result_list),
        # Guardrail aborts that cost an attempt. Reported, not gated: the
        # retry keeps them out of the metrics, so this is the only place the
        # run's real infra cost stays visible.
        "degraded_attempts": degraded_attempts,
        "grounding_rate": grounding_rate(result_list),
        "hallucination_rate": hallucination_rate(result_list),
        "unanswerable_declined": declined_unanswerables(results, questions),
        "expected_fact_coverage": coverage,
        # The gate counts `filed` only; the split is what keeps the
        # diagnostic a lenient metric would destroy (ADR 0020).
        "expected_fact_buckets": {
            bucket: sum(1 for row in coverage_rows if row.bucket == bucket)
            for bucket in ("filed", "derived", "absent", "unresolved")
        },
        "expected_fact_labels": [
            {
                "question": row.question_id,
                "label": row.description,
                "value": row.value,
                "bucket": row.bucket,
            }
            for row in coverage_rows
        ],
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
        judgments, judge_failures = judge_citations(result_list, settings)
        metrics["citation_accuracy"] = citation_accuracy(judgments)
        metrics["citation_judged"] = len(judgments)
        metrics["citation_judge_failures"] = judge_failures
        # Per-pair verdicts, so diagnosis never needs a paid re-judge.
        metrics["citation_judgments"] = {j.claim_id: j.supported for j in judgments}
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
        if isinstance(value, dict | list):
            continue
        typer.echo(f"{key}: {value:.3f}" if isinstance(value, float) else f"{key}: {value}")
    buckets = metrics["expected_fact_buckets"]
    typer.echo("expected facts: " + ", ".join(f"{k} {v}" for k, v in buckets.items()))
    typer.echo(f"Report: {report_path}")

    # A dropped question silently shrinks every metric's denominator — the
    # 2026-07-27 run lost g-010 this way. Fail loudly instead.
    dropped = metrics["n_completed"] < metrics["n_questions"]
    if dropped:
        typer.secho(
            f"INCOMPLETE RUN: {metrics['n_completed']}/{metrics['n_questions']} questions "
            "completed — metrics are computed over a shrunken denominator",
            fg=typer.colors.RED,
        )

    if write_baseline:
        target_misses = _gate(metrics, TARGETS)
        if (dropped or target_misses) and not force:
            for miss in target_misses:
                typer.secho(f"TARGET MISSED: {miss}", fg=typer.colors.RED)
            typer.secho(
                "Refusing to accept this run as the baseline (--force to override).",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        gated = {
            k: v
            for k, v in metrics.items()
            if isinstance(v, int | float)
            and not isinstance(v, bool)
            and k.startswith(
                (
                    "grounding",
                    "hallucination",
                    "citation_accuracy",
                    "numeric",
                    "verifier",
                    "expected_fact_coverage",
                )
            )
        }
        baselines_path.write_text(
            json.dumps(
                {
                    "accepted": stamp,
                    "n_questions": metrics["n_questions"],
                    "n_completed": metrics["n_completed"],
                    "metrics": gated,
                },
                indent=2,
            )
            + "\n"
        )
        typer.echo(f"Baseline written: {baselines_path}")
        return

    if dropped:
        raise typer.Exit(code=1)

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
