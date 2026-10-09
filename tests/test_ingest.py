"""Ingestion: store idempotency, table extraction, chunking, facts."""

import itertools
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import edgar
import pytest
from pydantic import SecretStr

from fie.config import Settings
from fie.ingest import pipeline
from fie.ingest.chunk import chunk_filing, split_with_overlap
from fie.ingest.fetch import AcquiredFiling
from fie.ingest.parse import ParsedFiling, ParsedTable, extract_tables, fiscal_period_label
from fie.ingest.store import (
    STATUS_ACQUIRED,
    STATUS_CHUNKED,
    STATUS_FAILED,
    Fact,
    IngestStore,
    ManifestRow,
)

# --- Store ---


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "ingest.db")


def make_fact(**overrides: object) -> Fact:
    defaults: dict = {
        "ticker": "TSLA",
        "concept": "us-gaap:Revenues",
        "label": "Total revenues",
        "value": 96_773_000_000.0,
        "unit": "usd",
        "decimals": -6,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "fiscal_period": "FY",
        "fiscal_year": 2025,
        "accession_no": "acc-1",
        "form_type": "10-K",
    }
    defaults.update(overrides)
    return Fact(**defaults)


def test_manifest_upsert_is_idempotent(store: IngestStore) -> None:
    row = ManifestRow(
        accession_no="acc-1", ticker="TSLA", form_type="10-K", filing_date="2026-01-29"
    )
    store.upsert_manifest(row)
    store.upsert_manifest(row)
    assert len(store.list_manifest()) == 1


def test_manifest_status_progression_and_filtering(store: IngestStore) -> None:
    store.upsert_manifest(
        ManifestRow(accession_no="a1", ticker="TSLA", form_type="10-K", filing_date="2026-01-29")
    )
    store.upsert_manifest(
        ManifestRow(accession_no="a2", ticker="AAPL", form_type="10-Q", filing_date="2026-02-01")
    )
    store.set_status("a1", STATUS_CHUNKED)
    assert [r.accession_no for r in store.list_manifest(STATUS_CHUNKED)] == ["a1"]
    assert [r.accession_no for r in store.list_manifest(STATUS_ACQUIRED)] == ["a2"]


def test_replace_facts_is_idempotent(store: IngestStore) -> None:
    store.replace_facts("acc-1", [make_fact()])
    store.replace_facts("acc-1", [make_fact()])
    assert store.count_facts() == 1


def test_query_after_close_raises_cleanly(store: IngestStore) -> None:
    """A timed-out verify branch's to_thread cannot be cancelled, so its
    reconcile may query after run teardown closed the store. That must be a
    clean Python error, not the C-level SQLITE_MISUSE/segfault that killed
    one question in each 2026-07-28 eval run (ADR 0019)."""
    store.replace_facts("acc-1", [make_fact()])
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.query_facts(concept_terms=["Revenues"])


def test_query_facts_by_concept_and_period(store: IngestStore) -> None:
    store.replace_facts(
        "acc-1",
        [
            make_fact(),
            make_fact(concept="us-gaap:NetIncomeLoss", label="Net income", value=7e9),
            make_fact(fiscal_year=2024, value=90e9, period_start="2024-01-01"),
        ],
    )
    hits = store.query_facts(concept_terms=["Revenues"], fiscal_year=2025, fiscal_period="FY")
    assert [f.value for f in hits] == [96_773_000_000.0]
    # Label substrings match too.
    assert store.query_facts(concept_terms=["net income"])[0].value == 7e9


def test_a_term_longer_than_the_filed_label_still_matches(store: IngestStore) -> None:
    """`%ProductsRevenue%` against a label of `Products` is unsatisfiable by
    construction, so every mapper term longer than the label it names could
    never match — g-013's services-vs-products growth comparison was ruled
    unsupported on that alone (ADR 0021)."""
    store.replace_facts(
        "acc-1",
        [
            make_fact(concept="us-gaap:RevenueFromContractWithCustomer", label="Products"),
            make_fact(
                concept="us-gaap:RevenueFromContractWithCustomer", label="Services", value=1e9
            ),
            # A generic label must not be reachable this way, or it would
            # match every compound term an analyst could write.
            make_fact(concept="us-gaap:AssetsFairValueDisclosure", label="Total", value=2e9),
        ],
    )
    assert [f.label for f in store.query_facts(concept_terms=["ProductsRevenue"])] == ["Products"]
    assert [f.label for f in store.query_facts(concept_terms=["ServicesRevenue"])] == ["Services"]
    assert store.query_facts(concept_terms=["TotalRevenues"]) == []


def test_non_dimensioned_facts_sort_first(store: IngestStore) -> None:
    store.replace_facts(
        "acc-1",
        [
            make_fact(dimensions={"srt:ProductOrServiceAxis": "Automotive"}, value=80e9),
            make_fact(),
        ],
    )
    hits = store.query_facts(concept_terms=["Revenues"])
    assert hits[0].dimensions is None
    assert hits[1].dimensions == {"srt:ProductOrServiceAxis": "Automotive"}


def test_exclude_dimensioned(store: IngestStore) -> None:
    store.replace_facts(
        "acc-1",
        [make_fact(dimensions={"axis": "a"}, value=1.0), make_fact()],
    )
    hits = store.query_facts(include_dimensioned=False)
    assert len(hits) == 1 and hits[0].dimensions is None


# --- Facts extraction ---


class _FakeXbrl:
    """Stands in for an edgartools XBRL object: `.facts.query().to_dataframe()`.

    Built through pandas on purpose — the defect this guards is a pandas
    artifact (a missing cell is NaN, not absent), so a plain list of dicts
    would not reproduce it.
    """

    def __init__(self, rows: list[dict]) -> None:
        from types import SimpleNamespace

        import pandas as pd

        query = SimpleNamespace(to_dataframe=lambda: pd.DataFrame(rows))
        self.facts = SimpleNamespace(query=lambda: query)


def test_instant_facts_keep_their_balance_date() -> None:
    """An instant's date arrives in `period_instant`, and its `period_end`
    cell is NaN — which is *truthy*, so a single `a or b` over the raw cells
    silently keeps the NaN and discards the date (ADR 0020). That left every
    balance-sheet fact dateless, which in turn defeated `_own_fiscal_year`:
    a prior-year comparative kept the filing's year and could ground a
    period-shifted claim, the exact defect ADR 0012 exists to prevent."""
    from fie.ingest.facts import extract_facts

    xbrl = _FakeXbrl(
        [
            {
                "concept": "us-gaap:Revenues",
                "label": "Total revenue",
                "numeric_value": 130_497e6,
                "unit_ref": "usd",
                "period_start": "2025-01-27",
                "period_end": "2026-01-25",
                "fiscal_period": "FY",
                "fiscal_year": 2026,
            },
            # The prior-year balance as it appears in the FY2026 10-K.
            {
                "concept": "us-gaap:Assets",
                "label": "Total assets",
                "numeric_value": 111_601e6,
                "unit_ref": "usd",
                "period_instant": "2025-01-26",
                "fiscal_year": 2026,
            },
        ]
    )
    flow, instant = extract_facts(xbrl, ticker="NVDA", accession_no="acc-1", form_type="10-K")
    assert (flow.period_start, flow.period_end, flow.fiscal_year) == (
        "2025-01-27",
        "2026-01-25",
        2026,
    )
    assert instant.period_end == "2025-01-26"
    assert instant.period_start is None  # an instant has one date, not a range
    assert instant.fiscal_year == 2025  # its own year, not the filing's 2026


@pytest.mark.parametrize(
    ("ticker", "period_end", "expected"),
    [
        ("NVDA", "2026-01-25", 2026),  # fiscal year-end itself
        ("NVDA", "2026-04-26", 2027),  # Q1 FY2027, a year past its calendar year
        ("AAPL", "2025-09-27", 2025),
        ("AAPL", "2025-12-27", 2026),  # Q1 FY2026
        ("TSLA", "2025-12-31", 2025),  # December year-end: never shifts
        ("TSLA", "2025-03-31", 2025),
    ],
)
def test_off_calendar_fiscal_years_shift_to_the_year_they_end_in(
    ticker: str, period_end: str, expected: int
) -> None:
    """A fiscal year is named for the calendar year it ends in, so an
    interim period ending after the company's year-end month belongs to the
    *next* fiscal year. Reading the period end's year verbatim mislabeled
    every AAPL and NVDA interim period (ADR 0020)."""
    from fie.ingest.facts import _own_fiscal_year

    assert _own_fiscal_year(period_end, None, ticker) == expected


# --- Table extraction ---

TABLE_HTML = """
<html><body>
<div>Item 7. Management's Discussion and Analysis</div>
<p>Some prose about revenue.</p>
<div>Revenue by segment (in millions)</div>
<table>
  <tr><td></td><td>2025</td><td></td><td>2024</td></tr>
  <tr><td>Automotive</td><td>77,070</td><td></td><td>72,480</td></tr>
  <tr><td>Energy</td><td>10,086</td><td></td><td>6,035</td></tr>
</table>
<table><tr><td>layout only</td></tr><tr><td>no digits here</td></tr></table>
<div>Item 8. Financial Statements</div>
<table>
  <tr><td>Assets</td><td>Total</td></tr>
  <tr><td>Cash</td><td>29,094</td></tr>
</table>
</body></html>
"""


def test_tables_extracted_with_item_attribution_and_captions() -> None:
    tables = extract_tables(TABLE_HTML)
    assert len(tables) == 2  # the digit-free layout table is dropped
    first, second = tables
    assert first.item == "Item 7"
    assert first.caption == "Revenue by segment (in millions)"
    assert first.headers == ["", "2025", "2024"]  # empty column dropped
    assert first.rows == [["Automotive", "77,070", "72,480"], ["Energy", "10,086", "6,035"]]
    assert second.item == "Item 8"


def test_table_markdown_rendering() -> None:
    table = ParsedTable(
        anchor="table-0001",
        item="Item 8",
        headers=["", "2025"],
        rows=[["Cash", "29,094"]],
    )
    md = table.to_markdown()
    assert "|  | 2025 |" in md
    assert "| Cash | 29,094 |" in md


# --- Chunking ---


def make_filing(**overrides: object) -> ParsedFiling:
    defaults: dict = {
        "ticker": "TSLA",
        "form_type": "10-K",
        "accession_no": "acc-1",
        "filing_date": "2026-01-29",
        "fiscal_period": "FY2025",
        "fiscal_year": 2025,
        "sections": {},
        "tables": [],
    }
    defaults.update(overrides)
    return ParsedFiling(**defaults)


def test_narrative_chunks_carry_full_citation_trail() -> None:
    filing = make_filing(sections={"Item 7": "Revenue grew.\n\nMargins fell."})
    chunks = chunk_filing(filing)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "acc-1:item-7-p001"
    assert chunk.ticker == "TSLA"
    assert chunk.metadata["accession_no"] == "acc-1"
    assert chunk.metadata["fiscal_period"] == "FY2025"
    assert chunk.metadata["item"] == "Item 7"
    assert chunk.metadata["chunk_type"] == "text"
    assert chunk.metadata["anchor"] == "item-7-p001"


def test_oversized_section_splits_with_overlap() -> None:
    paragraphs = [f"Paragraph {i}. " + "word " * 20 for i in range(30)]
    slices = split_with_overlap("\n\n".join(paragraphs), max_chars=1000, overlap_ratio=0.15)
    assert len(slices) > 1
    assert all(len(s) <= 1200 for s in slices)  # soft cap: overlap seed + paras
    # Consecutive slices share their boundary paragraph (the ~15% overlap;
    # best-effort — a paragraph larger than the window is not carried).
    for a, b in itertools.pairwise(slices):
        last_para_of_a = a.split("\n\n")[-1]
        assert last_para_of_a in b


def test_tables_are_atomic_chunks_with_structured_payload() -> None:
    table = ParsedTable(
        anchor="table-0001",
        item="Item 8",
        caption="Cash position",
        headers=["", "2025"],
        rows=[["Cash", "29,094"]],
    )
    filing = make_filing(tables=[table])
    chunks = chunk_filing(filing)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.metadata["chunk_type"] == "table"
    assert "29,094" in chunk.text
    assert "Cash position" in chunk.text  # caption embedded for retrieval
    assert '"rows"' in chunk.metadata["table_json"]


def test_oversized_table_splits_by_row_groups_repeating_headers() -> None:
    table = ParsedTable(
        anchor="table-0002",
        item="Item 8",
        headers=["Line", "Value"],
        rows=[[f"row-{i}", str(i)] for i in range(300)],
    )
    filing = make_filing(tables=[table])
    chunks = chunk_filing(filing, table_max_tokens=100)
    assert len(chunks) > 1
    assert all(c.metadata["chunk_type"] == "table" for c in chunks)
    assert all("| Line | Value |" in c.text for c in chunks)  # headers repeated
    ids = [c.chunk_id for c in chunks]
    assert len(set(ids)) == len(ids)


# --- Fiscal period labels ---


def test_fiscal_period_labels() -> None:
    assert fiscal_period_label("10-K", "2025-12-31") == ("FY2025", 2025)
    assert fiscal_period_label("10-Q", "2026-03-31") == ("Q1 2026", 2026)
    assert fiscal_period_label("8-K", "2026-07-02") == ("Q3 2026", 2026)
    assert fiscal_period_label("10-K", None) == ("", None)


# --- Mangled 10-Q section re-split (ADR 0010 fallback) ---


def test_wellformed_sections_pass_through_unchanged() -> None:
    from fie.ingest.parse import resplit_mangled_sections

    sections = {"Item 7": "MD&A prose. " * 50, "Item 1A": "Risks. " * 50}
    assert resplit_mangled_sections(sections) is sections


def test_mangled_blob_is_resplit_with_part_transition() -> None:
    from fie.ingest.parse import resplit_mangled_sections

    financials = "Condensed statements content. " * 20
    mdna = "Discussion of results. " * 20
    legal = "Litigation content. " * 20
    blob = (
        f"{financials}\n"
        f"ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS\n{mdna}\n"
        f"ITEM 1. LEGAL PROCEEDINGS\n{legal}"
    )
    result = resplit_mangled_sections({"Part I, Item 4": blob})
    assert set(result) == {"Part I, Item 1", "Part I, Item 2", "Part II, Item 1"}
    assert "Condensed statements" in result["Part I, Item 1"]
    assert "Discussion of results" in result["Part I, Item 2"]
    assert "Litigation content" in result["Part II, Item 1"]


def test_overlapping_blobs_merge_keeping_longest_per_item() -> None:
    from fie.ingest.parse import resplit_mangled_sections

    long_mdna = "ITEM 2. MANAGEMENT'S DISCUSSION\n" + "Full discussion. " * 40
    short_mdna = "ITEM 2. MANAGEMENT'S DISCUSSION\n" + "Truncated. " * 20
    exhibits = "ITEM 6. EXHIBITS\n" + "Exhibit index. " * 20
    result = resplit_mangled_sections(
        {"Part I, Item 4": long_mdna + "\n" + exhibits, "Part II, Item 4": short_mdna}
    )
    assert "Full discussion." in result["Part I, Item 2"]
    assert "Truncated." not in result["Part I, Item 2"]


def test_mid_sentence_item_references_do_not_split() -> None:
    from fie.ingest.parse import resplit_mangled_sections

    text = (
        "See Item 1A. Risk Factors for details, and also refer to "
        "item 3. of this report for legal matters. "
    ) * 20
    sections = {"Item 7": text}
    assert resplit_mangled_sections(sections) is sections


# --- Pipeline: a failure stays with its filing ---


def test_a_failed_filing_is_isolated_and_retried_on_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One filing that cannot be acquired must not cost the others, and the
    re-run that resumability promises must retry it — and only it. EDGAR is a
    network dependency with its own rate limits, so a partial ingest is the
    normal case, not the exceptional one."""
    filings = [
        SimpleNamespace(accession_no=acc, form="10-K", filing_date="2026-01-29")
        for acc in ("acc-good", "acc-bad")
    ]
    acquired: list[str] = []

    def acquire(filing: SimpleNamespace, *, ticker: str, raw_dir: Path) -> AcquiredFiling:
        acquired.append(filing.accession_no)
        if filing.accession_no == "acc-bad":
            raise ConnectionError("EDGAR reset the connection")
        return AcquiredFiling(
            parsed=make_filing(accession_no=filing.accession_no),
            facts=[],
            raw_path=str(raw_dir / filing.accession_no),
            checksum="sha",
        )

    monkeypatch.setattr(pipeline, "enumerate_filings", lambda ticker, years: filings)
    monkeypatch.setattr(pipeline, "acquire_filing", acquire)
    monkeypatch.setattr(edgar, "set_identity", lambda identity: None)
    settings = Settings(
        _env_file=None,
        anthropic_api_key=SecretStr("x"),
        data_dir=tmp_path,
        edgar_identity="Test Runner test@example.com",
    )

    first = pipeline.run_ingest(settings, tickers=["TSLA"], years=1, skip_embed=True)
    store = IngestStore(settings.ingest_db_path)
    bad, good = store.get_manifest("acc-bad"), store.get_manifest("acc-good")
    store.close()
    assert bad is not None and bad.status == STATUS_FAILED
    assert "EDGAR reset the connection" in (bad.detail or "")
    assert good is not None and good.status == STATUS_CHUNKED
    assert first.acquired == 1 and len(first.failures) == 1

    acquired.clear()
    pipeline.run_ingest(settings, tickers=["TSLA"], years=1, skip_embed=True)
    assert acquired == ["acc-bad"]


def test_ingest_without_an_edgar_identity_fails_before_any_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC requires a real name and email in the user agent and refuses
    noreply addresses, and the repo carries no personal data, so the default
    is empty. An empty identity must stop the run before EDGAR is contacted,
    not after a 403."""

    def contacted(ticker: str, years: int) -> list[SimpleNamespace]:
        raise AssertionError("EDGAR was contacted without an identity")

    monkeypatch.setattr(pipeline, "enumerate_filings", contacted)
    settings = Settings(_env_file=None, anthropic_api_key=SecretStr("x"), data_dir=tmp_path)

    with pytest.raises(RuntimeError, match="EDGAR_IDENTITY is not set"):
        pipeline.run_ingest(settings, tickers=["TSLA"], years=1, skip_embed=True)
