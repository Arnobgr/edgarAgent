"""The facts store on the research path (ADR 0022): the architectural half
of decision §3.3 that was never built. The system could check a number it
could not look up."""

from pathlib import Path
from typing import Any

import pytest

from fie.context import pack_evidence_by_sub_question
from fie.ingest.store import Fact, IngestStore
from fie.state import EvidenceChunk, SubQuestion
from fie.tools.fact_lookup import (
    INJECTED_SCORE,
    MAX_FACTS,
    FactLookupOutput,
    FactRequest,
    XbrlFactLookup,
)


class StubMapper:
    """Returns a fixed mapping; records the questions it was asked."""

    def __init__(self, output: Any) -> None:
        self.output = output
        self.questions: list[str] = []

    def invoke(self, messages: list[tuple[str, str]]) -> Any:
        self.questions.append(messages[-1][1])
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def make_fact(**overrides: Any) -> Fact:
    defaults: dict[str, Any] = {
        "ticker": "NVDA",
        "concept": "us-gaap:Assets",
        "label": "Total assets",
        "value": 206_803e6,
        "unit": "usd",
        "decimals": -6,
        "period_start": None,
        "period_end": "2026-01-25",
        "fiscal_period": None,
        "fiscal_year": 2026,
        "accession_no": "0001045810-26-000021",
        "form_type": "10-K",
    }
    defaults.update(overrides)
    return Fact(**defaults)


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    store = IngestStore(tmp_path / "ingest.db")
    store.replace_facts(
        "0001045810-26-000021",
        [
            make_fact(),
            # The same fiscal year's earlier balance dates.
            make_fact(value=161_148e6, period_end="2025-10-26"),
            make_fact(value=125_254e6, period_end="2025-04-27"),
            # A segment breakdown must never stand in for the company total.
            make_fact(value=999e6, label="Data Center", dimensions={"dimensioned": "true"}),
        ],
    )
    return store


def test_a_filed_figure_becomes_evidence_with_its_accession(store: IngestStore) -> None:
    """query_facts resolved all 28 expected figures while retrieval surfaced
    13 of them; the store was wired into verify and not research."""
    mapper = StubMapper(
        FactLookupOutput(
            requests=[
                FactRequest(ticker="NVDA", concept_terms=["us-gaap:Assets"], fiscal_year=2026)
            ]
        )
    )
    chunks = XbrlFactLookup(store, mapper).lookup("What were Nvidia's total assets?")
    store.close()

    assert len(chunks) == 1
    chunk = chunks[0]
    assert "206,803,000,000" in chunk.text
    assert chunk.metadata["accession_no"] == "0001045810-26-000021"
    assert chunk.ticker == "NVDA"
    # Not `table`: the reconciler's table fallback cell-matches chunk text,
    # and letting it match this would make the store corroborate itself.
    assert chunk.metadata["chunk_type"] == "xbrl"


def test_a_year_scoped_balance_resolves_to_the_year_end(store: IngestStore) -> None:
    """A fiscal year holds four balance dates. "At the end of FY2026" means
    the closing one — 206,803, not the 125,254 a quarter reported."""
    mapper = StubMapper(
        FactLookupOutput(
            requests=[
                FactRequest(ticker="NVDA", concept_terms=["us-gaap:Assets"], fiscal_year=2026)
            ]
        )
    )
    chunks = XbrlFactLookup(store, mapper).lookup("Nvidia's total assets at year end?")
    store.close()
    assert "206,803,000,000" in chunks[0].text
    assert "125,254" not in chunks[0].text


def test_a_qualitative_question_injects_nothing(store: IngestStore) -> None:
    """The mapper returns no requests for a question prose answers, so the
    research path is unchanged for the narrative half of the corpus."""
    mapper = StubMapper(FactLookupOutput(requests=[]))
    lookup = XbrlFactLookup(store, mapper)
    assert lookup.lookup("How does Nvidia describe export-control risk?") == []
    store.close()


def test_a_mapper_failure_injects_nothing_and_does_not_raise(store: IngestStore) -> None:
    """Injection is additive: a lookup failure must leave the branch with its
    retrieved prose, not kill it."""
    lookup = XbrlFactLookup(store, StubMapper(RuntimeError("provider down")))
    assert lookup.lookup("What were Nvidia's total assets?") == []
    store.close()


def test_dimensioned_facts_never_stand_in_for_the_total(store: IngestStore) -> None:
    mapper = StubMapper(
        FactLookupOutput(
            requests=[
                FactRequest(ticker="NVDA", concept_terms=["us-gaap:Assets"], fiscal_year=2026)
            ]
        )
    )
    chunks = XbrlFactLookup(store, mapper).lookup("Nvidia total assets?")
    store.close()
    assert all("Data Center" not in c.text for c in chunks)


def test_injected_facts_survive_the_fan_in_packing_cap(store: IngestStore) -> None:
    """Two answers had their figure retrieved and then cut by
    `chunks_per_sub_question` before the model saw it. An exact filed figure
    must not be droppable that way, so injected chunks outrank retrieved
    ones in the packing sort (ADR 0022)."""
    mapper = StubMapper(
        FactLookupOutput(
            requests=[
                FactRequest(ticker="NVDA", concept_terms=["us-gaap:Assets"], fiscal_year=2026)
            ]
        )
    )
    injected = XbrlFactLookup(store, mapper).lookup("Nvidia total assets?")
    store.close()
    assert injected[0].score == INJECTED_SCORE

    retrieved = [
        EvidenceChunk(
            chunk_id=f"c{i}",
            doc_id="d",
            ticker="NVDA",
            form_type="10-K",
            section="Item 7",
            text=f"prose {i}",
            score=0.03,
            sub_question_id="sq1-1",
        )
        for i in range(10)
    ]
    tagged = [c.model_copy(update={"sub_question_id": "sq1-1"}) for c in injected]
    packed = pack_evidence_by_sub_question(
        [*retrieved, *tagged],
        [SubQuestion(id="sq1-1", text="q", status="researched"), SubQuestion(id="sq1-2", text="x")],
        chunks_per_sub_question=2,
    )
    assert "206,803,000,000" in packed


def test_injection_is_capped(store: IngestStore) -> None:
    mapper = StubMapper(
        FactLookupOutput(
            requests=[
                FactRequest(ticker="NVDA", concept_terms=["us-gaap:Assets"], fiscal_year=year)
                for year in range(2020, 2020 + MAX_FACTS + 4)
            ]
            + [FactRequest(ticker="NVDA", concept_terms=["us-gaap:Assets"])] * 20
        )
    )
    assert len(XbrlFactLookup(store, mapper).lookup("everything")) <= MAX_FACTS
    store.close()
