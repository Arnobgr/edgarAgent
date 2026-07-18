"""Interim numeric reconciliation: normalization and matching edge cases."""

import pytest

from fie.tools.reconcile import InterimNumericReconciler, parse_numbers
from tests.fakes import make_chunks

RECONCILER = InterimNumericReconciler()


def _reconcile(claim: str, chunk_text: str) -> str:
    chunks = make_chunks({"c1": chunk_text})
    return RECONCILER.reconcile(claim, chunks).verdict


@pytest.mark.parametrize(
    ("claim", "chunk_text", "expected"),
    [
        # Scale-word claim vs bare figure under an "(In millions)" marker:
        # 391,035 million rounds to 391.0 billion at the claim's precision.
        (
            "Total net sales were $391.0 billion",
            "(In millions) Total net sales $ 391,035",
            "grounded",
        ),
        # Exact same figure, both in millions.
        ("Revenue was 391,035 million", "net sales of $391,035 million", "grounded"),
        # Boundary rounding: 96,995 million ≡ $96.99 billion (tolerance is
        # half a unit of the claim's last decimal, inclusive).
        ("Net income was $96.99 billion", "(in millions) net income of 96,995", "grounded"),
        # Percent precision: integer claim tolerates the decimal source.
        ("Gross margin was 46%", "gross margin percentage of 46.2%", "grounded"),
        ("Gross margin was 46.2%", "margin of 46.2 percent", "grounded"),
        # Scale words on both sides at different scales.
        ("a $2.5 million charge", "charges of 2,500 thousand", "grounded"),
        # Years are skipped on both sides; the real figure still matches.
        (
            "In fiscal 2024, net sales were $391.0 billion",
            "(In millions) Fiscal 2024 total net sales were 391,035",
            "grounded",
        ),
        # The planted lie: fabricated figure is not in the source.
        (
            "Total net sales were $500.0 billion",
            "(In millions) Total net sales $ 391,035",
            "unsupported",
        ),
        ("Revenue grew 15%", "revenue grew 5% year over year", "unsupported"),
        # A percent claim never matches a plain number.
        ("Margin was 46%", "headcount of 46,000 employees", "unsupported"),
        # Wrong precision: claim states more precision than the source has.
        ("Net sales were $391.55 billion", "(In millions) net sales of 391,035", "unsupported"),
        # One figure matches, one doesn't → partial.
        (
            "Revenue was $391.0 billion and grew 15%",
            "(In millions) net sales 391,035, up 2% from the prior year",
            "partial",
        ),
    ],
)
def test_reconciliation_table(claim: str, chunk_text: str, expected: str) -> None:
    assert _reconcile(claim, chunk_text) == expected


def test_grounded_outcome_carries_supporting_span() -> None:
    chunks = make_chunks({"c1": "(In millions) Total net sales $ 391,035 for fiscal 2024."})
    outcome = RECONCILER.reconcile("Total net sales were $391.0 billion", chunks)
    assert outcome.verdict == "grounded"
    assert outcome.supporting_span is not None
    assert "391,035" in outcome.supporting_span
    assert outcome.feedback is None


def test_unsupported_outcome_feedback_names_missing_figure_and_chunks() -> None:
    chunks = make_chunks({"c1": "(In millions) Total net sales $ 391,035."})
    outcome = RECONCILER.reconcile("Total net sales were $500.0 billion", chunks)
    assert outcome.verdict == "unsupported"
    assert "$500.0 billion" in (outcome.feedback or "")
    assert "c1" in (outcome.feedback or "")


def test_claim_with_no_parseable_number_is_unsupported() -> None:
    outcome = RECONCILER.reconcile("Revenue grew strongly", make_chunks())
    assert outcome.verdict == "unsupported"
    assert "no numeric value" in (outcome.feedback or "")


def test_no_cited_chunks_is_unsupported() -> None:
    outcome = RECONCILER.reconcile("Revenue was $1.0 billion", [])
    assert outcome.verdict == "unsupported"


def test_parse_numbers_normalization() -> None:
    numbers = parse_numbers("Sales hit $391.0 billion (up 2%) versus 365,817 in 2023.")
    values = [(n.value, n.is_percent) for n in numbers]
    assert (391.0e9, False) in values
    assert (2.0, True) in values
    assert (365817.0, False) in values
    # The bare year was skipped.
    assert all(n.value != 2023 for n in numbers)
