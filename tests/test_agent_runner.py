"""Agent-runner harness contracts (ADR 0015): the coverage denominator
never shrinks silently, and citation judging separates judge failures
from citation failures."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from fie.config import Settings
from fie.eval.agent_runner import expected_fact_coverage, judge_citations
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
            # Instant facts carry no fiscal_period: the same fiscal year
            # holds several balance dates (year-end + comparatives).
            Fact(
                ticker="TSLA",
                concept="us-gaap:Assets",
                label="Total assets",
                value=122_070_000_000.0,
                unit="usd",
                decimals=-6,
                period_start=None,
                period_end=None,
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
                period_end=None,
                fiscal_period=None,
                fiscal_year=2025,
                accession_no="acc-1",
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
    assert expected_fact_coverage(results, [question], eval_settings) == 0.5


def test_missing_result_counts_facts_as_misses(eval_settings: Settings) -> None:
    question = gold(
        "g-x", [ExpectedFact(ticker="TSLA", concept_terms=["us-gaap:Revenues"], fiscal_year=2025)]
    )
    assert expected_fact_coverage({}, [question], eval_settings) == 0.0


def test_instant_fact_matches_any_candidate_balance(eval_settings: Settings) -> None:
    question = gold(
        "g-x", [ExpectedFact(ticker="TSLA", concept_terms=["us-gaap:Assets"], fiscal_year=2025)]
    )
    results = {"g-x": run_result("Total assets were $106,618 million at year end.")}
    assert expected_fact_coverage(results, [question], eval_settings) == 1.0


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
