"""XBRL-exact reconciliation: mapping → lookup → verdict (fake mapper,
real SQLite store)."""

from pathlib import Path

import pytest

from fie.ingest.store import IngestStore
from fie.state import EvidenceChunk
from fie.tools.reconcile import (
    FactCandidate,
    FactMappingOutput,
    NumericReconciler,
    XbrlNumericReconciler,
)
from tests.test_ingest import make_fact  # the fact fixture factory


class FakeMapper:
    """Structured-output runnable stand-in: fixed FactMappingOutput."""

    def __init__(self, output: FactMappingOutput | Exception) -> None:
        self.output = output
        self.calls: list[object] = []

    def invoke(self, messages: object) -> FactMappingOutput:
        self.calls.append(messages)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts(
        "acc-1",
        [
            make_fact(),  # TSLA Revenues FY2025 = 96,773,000,000 usd
            make_fact(
                concept="us-gaap:NetIncomeLoss",
                label="Net income",
                value=7_091_000_000.0,
            ),
        ],
    )
    return s


def make_chunk(
    chunk_type: str = "text", text: str = "irrelevant", ticker: str = "TSLA"
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id="c1",
        doc_id=f"{ticker}-10-K-acc-1",
        ticker=ticker,
        form_type="10-K",
        section="Item 8",
        text=text,
        metadata={"chunk_type": chunk_type, "accession_no": "acc-1"},
    )


def revenue_mapping(value: float = 96.8e9) -> FactMappingOutput:
    return FactMappingOutput(
        candidates=[
            FactCandidate(
                value=value,
                concept_terms=["Revenues"],
                fiscal_year=2025,
                fiscal_period="FY",
                unit="usd",
            )
        ]
    )


def test_satisfies_protocol(store: IngestStore) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(revenue_mapping()))
    assert isinstance(reconciler, NumericReconciler)


def test_exact_match_at_stated_precision_grounds(store: IngestStore) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(revenue_mapping()))
    outcome = reconciler.reconcile(
        "TSLA total revenue for FY 2025 was $96.8 billion.", [make_chunk()]
    )
    assert outcome.verdict == "grounded"
    assert outcome.supporting_span is not None
    assert "us-gaap:Revenues" in outcome.supporting_span
    assert "acc-1" in outcome.supporting_span  # cited by accession number


def test_wrong_figure_is_unsupported_with_searched_feedback(store: IngestStore) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(revenue_mapping(120.0e9)))
    outcome = reconciler.reconcile(
        "TSLA total revenue for FY 2025 was $120.0 billion.", [make_chunk()]
    )
    assert outcome.verdict == "unsupported"
    assert outcome.feedback is not None
    assert "Revenues" in outcome.feedback  # names what was searched


def test_period_shift_is_caught(store: IngestStore) -> None:
    """The true value under the wrong fiscal year must NOT ground (the gap
    ADR 0005 called out in the interim reconciler)."""
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=96.8e9, concept_terms=["Revenues"], fiscal_year=2024, fiscal_period="FY"
            )
        ]
    )
    reconciler = XbrlNumericReconciler(store, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "TSLA total revenue for FY 2024 was $96.8 billion.", [make_chunk()]
    )
    assert outcome.verdict == "unsupported"


def test_table_chunk_fallback_when_no_fact_matches(store: IngestStore) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(FactMappingOutput()))
    table_chunk = make_chunk(
        chunk_type="table",
        text="Revenue by segment (in millions)\n\n| Automotive | 77,070 |",
    )
    outcome = reconciler.reconcile("Automotive revenue was $77,070 million.", [table_chunk])
    assert outcome.verdict == "grounded"
    assert outcome.supporting_span is not None and "77,070" in outcome.supporting_span


def test_text_chunks_do_not_serve_as_fallback(store: IngestStore) -> None:
    """The plan's fallback is table cells only — prose figures don't ground
    a numeric claim that XBRL can't confirm."""
    reconciler = XbrlNumericReconciler(store, FakeMapper(FactMappingOutput()))
    text_chunk = make_chunk(chunk_type="text", text="Revenue was $50.0 billion this year.")
    outcome = reconciler.reconcile("Revenue was $50.0 billion.", [text_chunk])
    assert outcome.verdict == "unsupported"


def test_mapper_failure_still_tries_table_fallback(store: IngestStore) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(RuntimeError("model down")))
    table_chunk = make_chunk(chunk_type="table", text="| Cash | 29,094 |")
    outcome = reconciler.reconcile("Cash was 29,094.", [table_chunk])
    assert outcome.verdict == "grounded"


def test_ticker_scope_comes_from_cited_chunks(store: IngestStore) -> None:
    """A figure only in TSLA's store must not ground an AAPL-cited claim."""
    reconciler = XbrlNumericReconciler(store, FakeMapper(revenue_mapping()))
    outcome = reconciler.reconcile(
        "Revenue for FY 2025 was $96.8 billion.", [make_chunk(ticker="AAPL")]
    )
    assert outcome.verdict == "unsupported"


def test_partial_when_one_of_two_figures_matches(store: IngestStore) -> None:
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=96.8e9, concept_terms=["Revenues"], fiscal_year=2025, fiscal_period="FY"
            ),
            FactCandidate(
                value=55.5e9, concept_terms=["GrossProfit"], fiscal_year=2025, fiscal_period="FY"
            ),
        ]
    )
    reconciler = XbrlNumericReconciler(store, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "Revenue was $96.8 billion and gross profit was $55.5 billion.", [make_chunk()]
    )
    assert outcome.verdict == "partial"
    assert outcome.feedback is not None and "55.5" in outcome.feedback


def test_no_numbers_in_claim_is_unsupported(store: IngestStore) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(FactMappingOutput()))
    outcome = reconciler.reconcile("Revenue grew strongly.", [make_chunk()])
    assert outcome.verdict == "unsupported"
