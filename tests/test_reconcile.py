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
        # Accounting-negative parentheses: "(8)%" is a percent, matched
        # sign-blind against the claim's worded direction ("declined 8%").
        (
            "In fiscal 2024, Greater China net sales declined 8% to $66,952 million",
            "(dollars in millions) Greater China64,377 (4)%66,952 (8)%72,559",
            "grounded",
        ),
        ("a net loss of $1,234 million", "(in millions) net loss of $(1,234)", "grounded"),
        # A parenthesized percent still never matches a different figure.
        ("Greater China declined 12%", "Greater China66,952 (8)%72,559", "unsupported"),
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


def test_parse_numbers_parenthesized_negatives() -> None:
    numbers = parse_numbers("Greater China64,377 (4)%66,952 (8)%72,559")
    values = [(n.value, n.is_percent) for n in numbers]
    assert (4.0, True) in values
    assert (8.0, True) in values
    assert (66952.0, False) in values


def test_parse_numbers_suffix_scales() -> None:
    """ "$298,085M" must scale like "$298,085 million". Without this a figure
    parses 10^6 too small and can never reconcile against the facts store —
    it cost the 2026-07-27 re-baseline 18 of its 28 numeric_fidelity misses
    (ADR 0017)."""
    numbers = parse_numbers("Products $298,085M vs $71.6B, plus $1.5bn and $25.0mm")
    values = [n.value for n in numbers]
    assert values == [298_085e6, 71.6e9, 1.5e9, 25.0e6]
    assert all(n.explicit_scale for n in numbers)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A letter suffix needs a word boundary, or any capitalised word
        # starting with M or B would scale the figure before it.
        ("the Model 3 and $5 Model Y", [3.0, 5.0]),
        ("$5 basis points", [5.0]),
        ("capacity of 100 MWh", [100.0]),
        ("filed a 10-K", [10.0]),
    ],
)
def test_parse_numbers_suffix_false_positives(text: str, expected: list[float]) -> None:
    assert [n.value for n in parse_numbers(text)] == expected


def test_parse_numbers_normalization() -> None:
    numbers = parse_numbers("Sales hit $391.0 billion (up 2%) versus 365,817 in 2023.")
    values = [(n.value, n.is_percent) for n in numbers]
    assert (391.0e9, False) in values
    assert (2.0, True) in values
    assert (365817.0, False) in values
    # The bare year was skipped.
    assert all(n.value != 2023 for n in numbers)
