"""Eval metrics: pure-function checks on tiny fixtures."""

import json
from pathlib import Path

from fie.eval.datasets import LabeledClaim, SectionRef
from fie.eval.figures import build_figure_queries, mantissas
from fie.eval.metrics import (
    CitationJudgment,
    citation_accuracy,
    figure_rank,
    figure_recall_at_k,
    grounding_rate,
    hallucination_rate,
    mrr,
    ndcg_at_10,
    numeric_fidelity,
    recall_at_k,
    verifier_scores,
)
from fie.ingest.store import Fact, IngestStore
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


def test_figure_rank_finds_the_chunk_carrying_the_figure() -> None:
    """Section labels credit any chunk of the right section; figure labels
    credit only the chunk that prints the number (ADR 0020)."""
    retrieved = [chunk("Item 1A"), chunk("Item 7"), chunk("Item 8")]
    assert figure_rank(retrieved, ["TSLA-Item 8"]) == 3
    assert figure_rank(retrieved, ["TSLA-Item 99"]) is None
    assert figure_recall_at_k([3, None, 1], k=2) == 1 / 3
    assert figure_recall_at_k([3, None, 1], k=3) == 2 / 3


def test_mantissas_need_four_significant_digits() -> None:
    """The 3-digit billions form matches arbitrary filing text — "161" for
    161,148,000,000 produced two spurious "the figure was available"
    readings during the 2026-07-30 diagnosis."""
    assert mantissas(94_827_000_000.0) == [
        "94,827",
        "94827",
        "94,827,000",
        "94827000",
        "94,827,000,000",
        "94827000000",
    ]
    # 161 billion has no 4-digit rendering shorter than its thousands form.
    assert "161" not in mantissas(161_000_000_000.0)
    # Per-share and ratio values have no filing-scale mantissa at all.
    assert mantissas(1.26) == []


def test_figure_labels_target_the_year_end_balance_not_a_quarter(tmp_path: Path) -> None:
    """A fiscal year holds four balance dates for a concept. Labeling any of
    them "FY2026 total assets" is the defect that made NVDA's total assets
    unscoreable (ADR 0020), so the generator keeps only the closing one."""
    store = IngestStore(tmp_path / "ingest.db")
    store.replace_facts(
        "acc-1",
        [
            Fact(
                ticker="NVDA",
                concept="us-gaap:Assets",
                label="Total assets",
                value=value,
                unit="usd",
                decimals=-6,
                period_start=None,
                period_end=date,
                fiscal_period=None,
                fiscal_year=2026,
                accession_no="acc-1",
                form_type="10-K",
            )
            for value, date in ((125_254e6, "2025-04-27"), (206_803e6, "2026-01-25"))
        ],
    )
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "acc-1.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {
                        "chunk_id": "acc-1:table-1",
                        "ticker": "NVDA",
                        "text": "Total assets 206,803 125,254",
                    }
                ]
            }
        )
    )
    queries = build_figure_queries(store, chunks_dir, [])
    store.close()
    assert [(q.id, q.value) for q in queries] == [("fq-NVDA-Assets-2026", 206_803e6)]
    assert queries[0].relevant_chunk_ids == ["acc-1:table-1"]


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


def test_numeric_fidelity_applies_answer_context_scale() -> None:
    # Markdown tables render "(in millions)" headers with bare-$ cells; the
    # figure's true magnitude is the cell times the context scale, the same
    # rule the reconciler applies to source chunks (ADR 0019).
    answer = "Revenue by quarter (in millions):\n| Q1 | $23,329 |"
    assert numeric_fidelity([result([], answer=answer)], [23_329_000_000.0]) == 1.0
    # An explicit scale word on the figure wins over the context marker.
    answer = "Revenue by quarter (in millions):\n| FY | $96.8 billion |"
    assert numeric_fidelity([result([], answer=answer)], [96_773_000_000.0]) == 1.0


def test_numeric_fidelity_scores_only_financial_figures() -> None:
    # Citation markup, dates, ordinals and product names must not be scored:
    # only the $96.8 billion figure counts (ADR 0015).
    answer = (
        "1. On September 30, 2025, revenue was $96.8 billion for the H200 line "
        "[0001045810-25-045968:table-0011] per the 10-K."
    )
    assert numeric_fidelity([result([], answer=answer)], [96_773_000_000.0]) == 1.0
    assert numeric_fidelity([result([], answer=answer)], [123.0]) == 0.0


def test_numeric_fidelity_ignores_retracted_claims_section() -> None:
    from fie.state import RETRACTED_HEADER

    answer = f"Revenue was $96.8 billion.\n\n{RETRACTED_HEADER}\n\n- Profit was $55.5 billion."
    assert numeric_fidelity([result([], answer=answer)], [96_773_000_000.0]) == 1.0


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
