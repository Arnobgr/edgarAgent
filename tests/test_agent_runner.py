"""Agent-runner harness contracts (ADR 0015): the coverage denominator
never shrinks silently, citation judging separates judge failures from
citation failures, and a crash is never scored as a decline."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from fie.config import Settings
from fie.eval.agent_runner import (
    _RATCHETED_PREFIXES,
    TARGETS,
    _gate,
    _measured,
    _ratcheted,
    _run_gold,
    declined_unanswerables,
    expected_fact_coverage,
    judge_citations,
)
from fie.eval.datasets import EVAL_DIR, ExpectedFact, GoldQuestion
from fie.eval.metrics import grounding_rate, hallucination_rate, numeric_fidelity
from fie.ingest.store import Fact, IngestStore
from fie.state import GroundingRow, JudgeOutput, RunResult, SourceRef


@pytest.fixture
def eval_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("sk-ant-test"),
        data_dir=tmp_path,
    )
    store = IngestStore(settings.ingest_db_path)
    store.replace_facts(
        "acc-1",
        [
            Fact(
                ticker="TSLA",
                concept="us-gaap:Revenues",
                label="Total revenues",
                value=96_773_000_000.0,
                unit="usd",
                decimals=-6,
                period_start="2025-01-01",
                period_end="2025-12-31",
                fiscal_period="FY",
                fiscal_year=2025,
                accession_no="acc-1",
                form_type="10-K",
            ),
            # Instant facts carry no fiscal_period and one date each: a
            # fiscal year holds four balance dates for a concept, so only
            # the date names the year-end figure (ADR 0020).
            Fact(
                ticker="TSLA",
                concept="us-gaap:Assets",
                label="Total assets",
                value=122_070_000_000.0,
                unit="usd",
                decimals=-6,
                period_start=None,
                period_end="2025-12-31",
                fiscal_period=None,
                fiscal_year=2025,
                accession_no="acc-1",
                form_type="10-K",
            ),
            Fact(
                ticker="TSLA",
                concept="us-gaap:Assets",
                label="Total assets",
                value=106_618_000_000.0,
                unit="usd",
                decimals=-6,
                period_start=None,
                period_end="2025-09-30",
                fiscal_period=None,
                fiscal_year=2025,
                accession_no="acc-1",
                form_type="10-K",
            ),
            Fact(
                ticker="AAPL",
                concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                label="Total net sales",
                value=416_161_000_000.0,
                unit="usd",
                decimals=-6,
                period_start="2024-09-29",
                period_end="2025-09-27",
                fiscal_period="FY",
                fiscal_year=2025,
                accession_no="acc-2",
                form_type="10-K",
            ),
            Fact(
                ticker="NVDA",
                concept="us-gaap:ResearchAndDevelopmentExpense",
                label="Research and development",
                value=18_497_000_000.0,
                unit="usd",
                decimals=-6,
                period_start="2025-01-27",
                period_end="2026-01-25",
                fiscal_period="FY",
                fiscal_year=2026,
                accession_no="acc-3",
                form_type="10-K",
            ),
        ],
    )
    store.close()
    return settings


def gold(qid: str, facts: list[ExpectedFact]) -> GoldQuestion:
    return GoldQuestion(
        id=qid, question="q", category="single_numeric", answerable=True, expected_facts=facts
    )


def run_result(answer: str) -> RunResult:
    return RunResult(run_id="run-1", question="q", answer=answer)


def test_unresolvable_expected_fact_counts_as_miss(eval_settings: Settings) -> None:
    question = gold(
        "g-x",
        [
            ExpectedFact(ticker="TSLA", concept_terms=["us-gaap:Revenues"], fiscal_year=2025),
            ExpectedFact(ticker="TSLA", concept_terms=["NoSuchConcept"], fiscal_year=2025),
        ],
    )
    results = {"g-x": run_result("Revenue was $96,773 million.")}
    # The unresolvable fact stays in the denominator: 1 of 2, not 1 of 1.
    coverage, rows = expected_fact_coverage(results, [question], eval_settings)
    assert coverage == 0.5
    assert [row.bucket for row in rows] == ["filed", "unresolved"]


def test_missing_result_counts_facts_as_misses(eval_settings: Settings) -> None:
    question = gold(
        "g-x", [ExpectedFact(ticker="TSLA", concept_terms=["us-gaap:Revenues"], fiscal_year=2025)]
    )
    assert expected_fact_coverage({}, [question], eval_settings)[0] == 0.0


def test_instant_label_resolves_to_one_balance_date(eval_settings: Settings) -> None:
    """A fiscal year holds several balance dates for one concept. The old
    metric accepted any of them, so NVDA's total assets could not be scored
    at all — the 5 candidates it sampled never included the year-end value
    (ADR 0020). The label's date now names exactly one figure."""
    question = gold(
        "g-x",
        [
            ExpectedFact(
                ticker="TSLA",
                concept_terms=["us-gaap:Assets"],
                fiscal_year=2025,
                period_end="2025-12-31",
            )
        ],
    )
    quarter_end = {"g-x": run_result("Total assets were $106,618 million at year end.")}
    year_end = {"g-x": run_result("Total assets were $122,070 million at year end.")}
    assert expected_fact_coverage(quarter_end, [question], eval_settings)[0] == 0.0
    assert expected_fact_coverage(year_end, [question], eval_settings)[0] == 1.0


def test_filed_figure_at_coarser_precision_is_covered(eval_settings: Settings) -> None:
    """g-015 said "$416 billion" against a filed 416,161. That *is* the
    filed figure, stated less precisely — which ADR 0012's stated-precision
    rule already admits everywhere else in the system, so coverage admits it
    too rather than inventing a second definition of the same number."""
    question = gold(
        "g-x",
        [
            ExpectedFact(
                ticker="AAPL",
                concept_terms=["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"],
                fiscal_year=2025,
                fiscal_period="FY",
            )
        ],
    )
    results = {"g-x": run_result("Apple's full-year FY2025 revenue was $416 billion.")}
    coverage, rows = expected_fact_coverage(results, [question], eval_settings)
    assert (coverage, rows[0].bucket) == (1.0, "filed")


def test_derived_figure_near_the_filed_one_is_not_covered(eval_settings: Settings) -> None:
    """g-003 computed "$18,571 million" from 8.6% of revenue against a filed
    18,497 — a different number that lands inside a flat 0.5% band. Crediting
    it would blind the only instrument that detects "the fact was in the
    corpus and we failed to surface it", so it is recorded as `derived` and
    never counted."""
    question = gold(
        "g-x",
        [
            ExpectedFact(
                ticker="NVDA",
                concept_terms=["us-gaap:ResearchAndDevelopmentExpense"],
                fiscal_year=2026,
                fiscal_period="FY",
            )
        ],
    )
    results = {"g-x": run_result("This implies R&D spending of about $18,571 million.")}
    coverage, rows = expected_fact_coverage(results, [question], eval_settings)
    assert (coverage, rows[0].bucket) == (0.0, "derived")


def test_a_declining_system_scores_a_perfect_gate_except_on_recall(
    eval_settings: Settings,
) -> None:
    """The hole recall exists to close: an answer that asserts nothing has no
    ungrounded claims, no surviving hallucinations and no wrong figures, so
    every quality metric reads perfect. Only coverage notices that the
    question went unanswered (ADR 0020)."""
    question = gold(
        "g-x",
        [
            ExpectedFact(
                ticker="TSLA",
                concept_terms=["us-gaap:Revenues"],
                fiscal_year=2025,
                fiscal_period="FY",
            )
        ],
    )
    refusal = run_result("The provided evidence does not contain Tesla's total revenue.")
    assert grounding_rate([refusal]) == 0.0  # no claims at all
    assert hallucination_rate([refusal]) == 0.0
    assert numeric_fidelity([refusal], [96_773_000_000.0]) == 1.0
    assert expected_fact_coverage({"g-x": refusal}, [question], eval_settings)[0] == 0.0


def unanswerable(qid: str) -> GoldQuestion:
    return GoldQuestion(id=qid, question="q", category="unanswerable", answerable=False)


def test_errored_empty_answer_is_not_a_decline() -> None:
    question = unanswerable("g-u")
    aborted = RunResult(run_id="run-1", question="q", answer="", error="budget exceeded")
    assert declined_unanswerables({"g-u": aborted}, [question]) == 0.0


def test_declining_answer_with_no_surviving_numeric_counts() -> None:
    question = unanswerable("g-u")
    declined = RunResult(
        run_id="run-1", question="q", answer="The filings do not disclose this figure."
    )
    assert declined_unanswerables({"g-u": declined}, [question]) == 1.0


class StubJudge:
    """Pops rulings; an Exception instance in the queue is raised."""

    def __init__(self, rulings: list[object]) -> None:
        self.rulings = rulings

    def invoke(self, messages: object) -> object:
        ruling = self.rulings.pop(0)
        if isinstance(ruling, Exception):
            raise ruling
        return ruling


def judged_result() -> RunResult:
    rows = [
        GroundingRow(
            claim_id=f"claim-1-{i}",
            text="t",
            claim_type="qualitative",
            status="grounded",
            verdict="grounded",
            supporting_span="span",
            sources=[SourceRef(chunk_id="c")],
        )
        for i in range(3)
    ]
    return RunResult(run_id="run-1", question="q", answer="a", grounding=rows)


def test_judge_failures_are_counted_not_scored(eval_settings: Settings) -> None:
    judge = StubJudge(
        [
            JudgeOutput(verdict="grounded"),
            RuntimeError("api hiccup"),
            JudgeOutput(verdict="unsupported"),
        ]
    )
    judgments, failures = judge_citations([judged_result()], eval_settings, judge=judge)
    assert failures == 1
    # The failed pair is absent — not recorded as a bad citation.
    assert [(j.claim_id, j.supported) for j in judgments] == [
        ("run-1:claim-1-0", True),
        ("run-1:claim-1-2", False),
    ]


def queue_answers(monkeypatch: pytest.MonkeyPatch, results: list[RunResult | Exception]) -> None:
    """Serve `answer_question` from a queue, one entry per attempt."""

    async def fake(**kwargs: object) -> RunResult:
        outcome = results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("fie.eval.agent_runner.answer_question", fake)


async def test_guardrail_abort_costs_an_attempt_not_the_baseline(
    eval_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node timeout returns rather than raises, so only an `error` check
    catches it. Two synthesize stalls decided the 2026-07-31 run this way."""
    queue_answers(
        monkeypatch,
        [
            RunResult(run_id="r1", question="q", answer="stale", error="node 'synthesize' timed"),
            RunResult(run_id="r2", question="q", answer="clean"),
        ],
    )
    results, _run_ids, degraded = await _run_gold([gold("g-1", [])], eval_settings)
    assert results["g-1"].answer == "clean"
    assert degraded == 1


async def test_a_question_that_degrades_twice_is_still_counted(
    eval_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeping the last degraded result is deliberate: dropping the question
    would shrink every denominator instead of showing the bad number."""
    queue_answers(
        monkeypatch,
        [
            RunResult(run_id="r1", question="q", answer="a", error="budget exceeded"),
            RunResult(run_id="r2", question="q", answer="b", error="budget exceeded"),
        ],
    )
    results, _run_ids, degraded = await _run_gold([gold("g-1", [])], eval_settings)
    assert results["g-1"].error == "budget exceeded"
    assert degraded == 2


def test_the_claimless_count_is_reported_and_never_gates() -> None:
    """ADR 0038 is explicit that the point is the number ceasing to be
    invisible, not the number starting to fail runs. Two things hold that:
    it is absent from `TARGETS`, and `--write-baseline` records it beside
    `n_completed` rather than inside `metrics` — the only dict `_gate` reads
    on the next run. The second assertion is what would happen if it moved
    there: an improvement from 3 claimless runs to 0 reported as a
    regression, because nothing tells `_gate` which direction is better."""
    assert "runs_with_no_claims" not in TARGETS
    assert _gate({"runs_with_no_claims": 0}, {"runs_with_no_claims": 3}) == [
        "runs_with_no_claims: 0.000 below baseline 3.000"
    ]


def test_citation_accuracy_is_floored_not_ratcheted() -> None:
    """It had the two wrong halves and now has the two right ones (ADR 0039).

    It used to ratchet, without ever being declared a gate — the ratchet has
    no list and gates on membership in the written `metrics` block. The judge
    flips on about 1 in 27 byte-identical spans, so a reading carries ±0.037,
    and the 2026-08-06 drift run printed `REGRESSION` on a delta of 0.023.
    And it had no floor, so simply un-ratcheting it would have let a collapse
    exit 0.

    So: a floor coarse enough to ignore the noise, and no ratchet. Both halves
    of the de-gating are asserted, because either alone leaves the bug live —
    the writer must stop enrolling it, *and* the accepted baseline the next
    run actually reads must stop carrying it there, since a fixed writer
    leaves an armed file behind until a new baseline is written, which is
    months away by design."""
    assert TARGETS["citation_accuracy"] == 0.75
    assert _gate({"citation_accuracy": 0.40}, TARGETS) == [
        "citation_accuracy: 0.400 below baseline 0.750"
    ]
    assert _gate({"citation_accuracy": 0.828}, TARGETS) == []

    sample = {"citation_accuracy": 0.83, "grounding_rate": 0.95, "n_completed": 30}
    assert "citation_accuracy" not in _ratcheted(sample)

    accepted = json.loads((EVAL_DIR / "baselines.json").read_text())
    assert "citation_accuracy" not in accepted["metrics"]
    assert accepted["measured"]["citation_accuracy"] == pytest.approx(0.849, abs=0.001)


def test_only_an_invariant_is_ratcheted_and_every_score_has_a_floor() -> None:
    """ADR 0043. ADR 0039 fixed one metric and left the same defect armed on
    three more, because the ratchet has no list — it gates on whatever the
    written `metrics` block happens to hold.

    The arithmetic is what settled it. One claim of 200 is 0.005 of
    `grounding_rate`, one figure of ~95 is ~0.011 of `numeric_fidelity`, and
    one fact of 28 is 0.036 of `expected_fact_coverage`, so accepting a good
    run pins each of them within a claim or two of the next run's reading. The
    2026-08-06 drift run is the proof rather than the prediction: it met every
    target and still printed `REGRESSION`.

    `hallucination_rate` stays, because it is not a score. Retraction either
    removed every unsupported claim from the prose or it did not.

    Both halves are asserted again, for the reason ADR 0039 gives: a metric
    that leaves the ratchet without gaining a floor is not de-gated, it is
    unwatched. The second assertion is the one that would have caught this
    bug in July — every scored metric named in the report has an entry here."""
    assert _RATCHETED_PREFIXES == ("hallucination",)

    reading = {
        "grounding_rate": 0.980,
        "numeric_fidelity": 0.989,
        "expected_fact_coverage": 1.0,
        "hallucination_rate": 0.0,
        "verifier_recall_qualitative": 1.0,
    }
    assert _ratcheted(reading) == {"hallucination_rate": 0.0}
    assert _measured(reading) == reading

    # A run one claim, one figure and one fact worse than the accepted
    # baseline. Under the old prefixes this exited 1 on three counts.
    next_run = {
        "grounding_rate": 0.975,
        "numeric_fidelity": 0.978,
        "expected_fact_coverage": 0.964,
        "hallucination_rate": 0.0,
        "verifier_recall_qualitative": 1.0,
    }
    assert _gate(next_run, _ratcheted(reading)) == []
    assert _gate(next_run, TARGETS) == []

    # ...and a collapse still fails, which is what the floors are for.
    assert _gate({"grounding_rate": 0.40}, TARGETS) == [
        "grounding_rate: 0.400 below baseline 0.900"
    ]
    for name in reading:
        assert name in TARGETS
