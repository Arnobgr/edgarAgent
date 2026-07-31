"""Agent-runner harness contracts (ADR 0015): the coverage denominator
never shrinks silently, citation judging separates judge failures from
citation failures, and a crash is never scored as a decline."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from fie.config import Settings
from fie.eval.agent_runner import (
    declined_unanswerables,
    expected_fact_coverage,
    judge_citations,
)
from fie.eval.datasets import ExpectedFact, GoldQuestion
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
