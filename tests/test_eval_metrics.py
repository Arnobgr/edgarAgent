"""Eval metrics: pure-function checks on tiny fixtures."""

from fie.eval.datasets import LabeledClaim, SectionRef
from fie.eval.metrics import (
    CitationJudgment,
    citation_accuracy,
    grounding_rate,
    hallucination_rate,
    mrr,
    ndcg_at_10,
    numeric_fidelity,
    recall_at_k,
    verifier_scores,
)
from fie.state import EvidenceChunk, GroundingRow, RunResult

REL = [SectionRef(ticker="TSLA", form_type="10-K", section="Item 7")]


def chunk(
    section: str, ticker: str = "TSLA", form: str = "10-K", period: str = "FY2025"
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"{ticker}-{section}",
        doc_id=f"{ticker}-{form}-acc",
        ticker=ticker,
        form_type=form,
        section=section,
        text="…",
        metadata={"fiscal_period": period},
    )


def test_recall_at_k_counts_covered_sections() -> None:
    retrieved = [chunk("Item 1A"), chunk("Item 7")]
    assert recall_at_k(retrieved, REL, k=1) == 0.0
    assert recall_at_k(retrieved, REL, k=2) == 1.0


def test_section_match_is_substring_and_form_prefix() -> None:
    retrieved = [chunk("Item 7 - Management's Discussion", form="10-K405")]
    assert recall_at_k(retrieved, REL, k=1) == 1.0
    # Item 7 must not match Item 7A labels the other way round:
    assert (
        recall_at_k(
            [chunk("Item 7A")], [SectionRef(ticker="TSLA", form_type="10-K", section="Item 7A")], 1
        )
        == 1.0
    )


def test_mrr_first_relevant_rank() -> None:
    retrieved = [chunk("Item 1"), chunk("Item 7")]
    assert mrr(retrieved, REL) == 0.5
    assert mrr([chunk("Item 1")], REL) == 0.0


def test_ndcg_perfect_ranking_is_one() -> None:
    assert ndcg_at_10([chunk("Item 7")], REL) == 1.0
    assert 0.0 < ndcg_at_10([chunk("Item 1"), chunk("Item 7")], REL) < 1.0


def test_ndcg_stays_bounded_with_many_relevant_chunks() -> None:
    # Section labels make many chunks relevant; nDCG must stay <= 1.
    retrieved = [chunk("Item 7") for _ in range(10)]
    assert ndcg_at_10(retrieved, REL) == 1.0
    mixed = [chunk("Item 1"), *[chunk("Item 7") for _ in range(9)]]
    assert 0.0 < ndcg_at_10(mixed, REL) < 1.0


def result(
    rows: list[GroundingRow], retracted: list[GroundingRow] | None = None, answer: str = ""
) -> RunResult:
    return RunResult(
        run_id="r", question="q", answer=answer, grounding=rows, retracted=retracted or []
    )


def row(verdict: str | None, with_source: bool = True, claim_type: str = "numeric") -> GroundingRow:
    from fie.state import SourceRef

    return GroundingRow(
        claim_id="c1",
        text="t",
        claim_type=claim_type,  # type: ignore[arg-type]
        status="grounded" if verdict == "grounded" else "partial",
        verdict=verdict,  # type: ignore[arg-type]
        sources=[SourceRef(chunk_id="c")] if with_source else [],
    )


def test_grounding_rate_requires_verdict_and_source() -> None:
    results = [result([row("grounded"), row("grounded", with_source=False), row("partial")])]
    assert grounding_rate(results) == 1 / 3


def test_hallucination_rate_counts_unsupported_survivors_not_retractions() -> None:
    surviving = [row("grounded"), row("unsupported")]
    retracted = [row("unsupported")]
    results = [result(surviving, retracted=retracted)]
    assert hallucination_rate(results) == 1 / 2  # the retracted one doesn't count


def test_citation_accuracy() -> None:
    judgments = [
        CitationJudgment("a", True),
        CitationJudgment("b", True),
        CitationJudgment("c", False),
    ]
    assert citation_accuracy(judgments) == 2 / 3
    assert citation_accuracy([]) == 0.0


def test_numeric_fidelity_matches_store_values_excluding_percents() -> None:
    results = [result([], answer="Revenue was $96.8 billion, up 15%, vs $80.0 billion.")]
    fact_values = [96_773_000_000.0, 80_000_000_000.0]
    assert numeric_fidelity(results, fact_values) == 1.0  # the 15% is excluded
    assert numeric_fidelity(results, [96_773_000_000.0]) == 0.5


def test_verifier_scores_per_type() -> None:
    labeled = [
        LabeledClaim(id="1", text="t", claim_type="numeric", true_verdict="grounded"),
        LabeledClaim(id="2", text="t", claim_type="numeric", true_verdict="unsupported"),
        LabeledClaim(id="3", text="t", claim_type="qualitative", true_verdict="grounded"),
    ]
    predictions = {"1": "grounded", "2": "grounded", "3": "unsupported"}
    scores = {s.claim_type: s for s in verifier_scores(labeled, predictions)}  # type: ignore[misc]
    assert scores["numeric"].precision == 0.5  # accepted 2, good 1
    assert scores["numeric"].recall == 1.0
    assert scores["qualitative"].recall == 0.0
    assert scores["all"].n == 3
