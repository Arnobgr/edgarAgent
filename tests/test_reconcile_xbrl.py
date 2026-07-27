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


# --- ADR 0016: only financial figures are reconciliation obligations ---


def test_date_and_ordinal_fragments_are_not_obligations(store: IngestStore) -> None:
    """ "December 31", "Q4" and "10-K" must not block grounding — 18/53
    non-grounded claims in the 2026-07-27 baseline failed only on such
    fragments."""
    reconciler = XbrlNumericReconciler(store, FakeMapper(revenue_mapping()))
    outcome = reconciler.reconcile(
        "Per the 10-K, TSLA total revenue for FY 2025 (Q4, year ended "
        "December 31, 2025) was $96.8 billion.",
        [make_chunk()],
    )
    assert outcome.verdict == "grounded"


def test_claim_with_no_financial_figures_verifies_against_cited_text(
    store: IngestStore,
) -> None:
    reconciler = XbrlNumericReconciler(store, FakeMapper(FactMappingOutput()))
    chunk = make_chunk(text="We have experienced lead times of more than 12 months.")
    outcome = reconciler.reconcile("Lead times extended beyond 12 months.", [chunk])
    assert outcome.verdict == "grounded"
    outcome = reconciler.reconcile(
        "Lead times extended beyond 12 months.", [make_chunk(text="No numbers here.")]
    )
    assert outcome.verdict == "unsupported"


# --- ADR 0016: period-free retry (never year-free) ---


def test_instant_fact_with_null_period_grounds_despite_fy_mapping(tmp_path: Path) -> None:
    """Balance-sheet facts store fiscal_period = NULL; a mapping that says
    'FY' must still find them within the same fiscal year."""
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts(
        "acc-1",
        [make_fact(concept="us-gaap:Assets", label="Total assets", fiscal_period=None)],
    )
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=96.8e9, concept_terms=["Assets"], fiscal_year=2025, fiscal_period="FY"
            )
        ]
    )
    reconciler = XbrlNumericReconciler(s, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "TSLA total assets were $96.8 billion as of December 31, 2025.", [make_chunk()]
    )
    assert outcome.verdict == "grounded"
    assert outcome.supporting_span is not None and "us-gaap:Assets" in outcome.supporting_span


def test_ytd_fact_grounds_via_mapper_vocabulary(tmp_path: Path) -> None:
    """Nine-month figures live under 'YTD9' — reachable now that the
    mapping schema names YTD periods (7/53 baseline failures)."""
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts("acc-1", [make_fact(value=69_926_000_000.0, fiscal_period="YTD9")])
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=69.926e9, concept_terms=["Revenues"], fiscal_year=2025, fiscal_period="YTD9"
            )
        ]
    )
    reconciler = XbrlNumericReconciler(s, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "TSLA total revenue for the nine months ended September 30, 2025 was $69,926 million.",
        [make_chunk()],
    )
    assert outcome.verdict == "grounded"


def test_relaxed_pass_never_grounds_labeled_duration_facts(tmp_path: Path) -> None:
    """A full-year claim must not ground on a nine-month value in the same
    year: the relaxed pass admits only unlabeled instants. This is the
    vc-period-019 false accept caught in the verifier P/R run (ADR 0016)."""
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts(
        "acc-1",
        [
            make_fact(
                concept="us-gaap:NetIncomeLoss",
                label="Net income",
                value=7_069_000_000.0,
                fiscal_period="YTD9",
                fiscal_year=2023,
            )
        ],
    )
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=7.1e9, concept_terms=["NetIncomeLoss"], fiscal_year=2023, fiscal_period="FY"
            )
        ]
    )
    reconciler = XbrlNumericReconciler(s, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "TSLA's net income for FY 2023 was $7.1 billion.", [make_chunk()]
    )
    assert outcome.verdict == "unsupported"


def test_year_scoped_mapping_without_period_excludes_partial_year_facts(tmp_path: Path) -> None:
    """The mapper omits the period for 'FY 2023' claims in practice; a
    year-only mapping must still not ground on a YTD value — only FY and
    instant facts qualify (vc-period-019, ADR 0016)."""
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts(
        "acc-1",
        [
            make_fact(
                concept="us-gaap:NetIncomeLoss",
                label="Net income",
                value=7_069_000_000.0,
                fiscal_period="YTD9",
                fiscal_year=2023,
            ),
        ],
    )
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=7.1e9, concept_terms=["NetIncomeLoss"], fiscal_year=2023, fiscal_period=None
            )
        ]
    )
    reconciler = XbrlNumericReconciler(s, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "TSLA's net income for FY 2023 was $7.1 billion.", [make_chunk()]
    )
    assert outcome.verdict == "unsupported"

    # The same year-only mapping still finds the true full-year fact.
    s.replace_facts(
        "acc-1",
        [
            make_fact(
                concept="us-gaap:NetIncomeLoss",
                label="Net income",
                value=7_091_000_000.0,
                fiscal_period="FY",
                fiscal_year=2023,
            ),
        ],
    )
    outcome = reconciler.reconcile(
        "TSLA's net income for FY 2023 was $7.1 billion.", [make_chunk()]
    )
    assert outcome.verdict == "grounded"


def test_matching_is_sign_blind_on_the_fact_side(tmp_path: Path) -> None:
    """'$-23.0 million' parses as a magnitude; a fact stored as -23e6 must
    still match (vc-real-032, ADR 0016) — direction is verified in words."""
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts(
        "acc-1",
        [
            make_fact(
                concept="us-gaap:NetIncomeLossAttributableToNoncontrollingInterest",
                label="Net income (loss) attributable to noncontrolling interests",
                value=-23_000_000.0,
                fiscal_year=2023,
            ),
        ],
    )
    # The mapper echoes the printed sign; the parser is sign-blind. Both
    # sides must pair on magnitude.
    mapping = FactMappingOutput(
        candidates=[
            FactCandidate(
                value=-23.0e6,
                concept_terms=["NetIncomeLoss"],
                fiscal_year=2023,
                fiscal_period="FY",
            )
        ]
    )
    reconciler = XbrlNumericReconciler(s, FakeMapper(mapping))
    outcome = reconciler.reconcile(
        "TSLA's net income for FY 2023 was $-23.0 million.", [make_chunk()]
    )
    assert outcome.verdict == "grounded"


# --- ADR 0016: ratio percentages recompute from operand facts ---


def ratio_store(tmp_path: Path) -> IngestStore:
    s = IngestStore(tmp_path / "ingest.db")
    s.replace_facts(
        "acc-1",
        [
            make_fact(concept="us-gaap:GrossProfit", label="Gross profit", value=4_370_000_000.0),
            make_fact(value=23_350_000_000.0),  # Revenues
        ],
    )
    return s


def margin_mapping(value: float) -> FactMappingOutput:
    return FactMappingOutput(
        candidates=[
            FactCandidate(
                value=value,
                concept_terms=[],
                fiscal_year=2025,
                fiscal_period="FY",
                numerator_terms=["GrossProfit"],
                denominator_terms=["Revenues"],
            )
        ]
    )


def test_ratio_percent_grounds_by_recomputation(tmp_path: Path) -> None:
    reconciler = XbrlNumericReconciler(ratio_store(tmp_path), FakeMapper(margin_mapping(18.7)))
    # 4,370 / 23,350 = 18.72% — matches 18.7% at its printed precision.
    outcome = reconciler.reconcile("TSLA gross margin for FY 2025 was 18.7%.", [make_chunk()])
    assert outcome.verdict == "grounded"
    assert outcome.supporting_span is not None
    assert "GrossProfit" in outcome.supporting_span
    assert "Revenues" in outcome.supporting_span


def test_wrong_ratio_percent_stays_unsupported(tmp_path: Path) -> None:
    reconciler = XbrlNumericReconciler(ratio_store(tmp_path), FakeMapper(margin_mapping(25.0)))
    outcome = reconciler.reconcile("TSLA gross margin for FY 2025 was 25.0%.", [make_chunk()])
    assert outcome.verdict == "unsupported"
