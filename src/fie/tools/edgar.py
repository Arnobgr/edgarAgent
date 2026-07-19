"""EDGAR document source (edgartools).

Fetches a company's latest filing of a given form type — or, from Phase 2,
the latest 10-K plus recent 10-Qs as a naive multi-filing corpus — parses
them into sections via edgartools' structured accessors, and caches the
parsed results under the gitignored `data/` dir so reruns don't hit EDGAR.
Production ingestion of the full corpus is Phase 3.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from fie.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Sections shorter than this are boilerplate (e.g. "None." items) — skip them.
_MIN_SECTION_CHARS = 200


class FilingDocument(BaseModel):
    """A parsed filing: ordered sections plus the citation metadata."""

    doc_id: str
    ticker: str
    form_type: str
    accession_no: str
    filing_date: str
    sections: dict[str, str] = Field(default_factory=dict)


def fetch_filing(
    ticker: str, form_type: str = "10-K", settings: Settings | None = None
) -> FilingDocument:
    """Return the latest `form_type` filing for `ticker`, parsed into sections.

    The parsed document is cached as JSON under `data/filings/`; the cache is
    keyed by ticker + form type and holds whatever was latest when first
    fetched (fine for a fixed Phase 0 corpus — delete the file to refresh).
    """
    s = settings or get_settings()
    cache_path = s.data_dir / "filings" / f"{ticker.upper()}_{form_type.replace('/', '-')}.json"
    if cache_path.exists():
        logger.info("Loading cached filing from %s", cache_path)
        return FilingDocument.model_validate_json(cache_path.read_text())

    from edgar import Company, set_identity

    set_identity(s.edgar_identity)
    logger.info("Fetching latest %s for %s from EDGAR", form_type, ticker)
    filing = Company(ticker).get_filings(form=form_type).latest()
    doc = _parse_filing(filing, ticker, form_type)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(doc.model_dump_json(indent=2))
    logger.info("Cached %d sections to %s", len(doc.sections), cache_path)
    return doc


class _FilingSet(BaseModel):
    """JSON cache envelope for a multi-filing corpus."""

    documents: list[FilingDocument] = Field(default_factory=list)


def fetch_recent_filings(
    ticker: str,
    form_type: str = "10-K",
    quarters: int = 4,
    settings: Settings | None = None,
) -> list[FilingDocument]:
    """Return the latest `form_type` filing plus the last `quarters` 10-Qs —
    the Phase 2 naive corpus, wide enough that parallel research branches hit
    genuinely different documents.

    The annual filing reuses the `fetch_filing` cache; the quarterlies are
    cached together (keyed by ticker + count) and hold whatever was recent
    when first fetched — delete the file to refresh."""
    s = settings or get_settings()
    docs = [fetch_filing(ticker, form_type, s)]
    if quarters <= 0:
        return docs

    cache_path = s.data_dir / "filings" / f"{ticker.upper()}_10-Q_last{quarters}.json"
    if cache_path.exists():
        logger.info("Loading cached quarterly filings from %s", cache_path)
        docs.extend(_FilingSet.model_validate_json(cache_path.read_text()).documents)
        return docs

    from edgar import Company, set_identity

    set_identity(s.edgar_identity)
    logger.info("Fetching last %d 10-Q filings for %s from EDGAR", quarters, ticker)
    latest = Company(ticker).get_filings(form="10-Q").latest(quarters)
    filings = [latest] if quarters == 1 else list(latest)
    quarterlies = [_parse_filing(filing, ticker, "10-Q") for filing in filings]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(_FilingSet(documents=quarterlies).model_dump_json(indent=2))
    logger.info("Cached %d quarterly filings to %s", len(quarterlies), cache_path)
    return [*docs, *quarterlies]


def _parse_filing(filing: Any, ticker: str, form_type: str) -> FilingDocument:
    """Parse one edgartools filing into a FilingDocument (sections extracted)."""
    doc = FilingDocument(
        doc_id=f"{ticker.upper()}-{form_type}-{filing.accession_no}",
        ticker=ticker.upper(),
        form_type=form_type,
        accession_no=str(filing.accession_no),
        filing_date=str(filing.filing_date),
        sections=_extract_sections(filing),
    )
    if not doc.sections:
        raise RuntimeError(f"No sections could be extracted from {doc.doc_id}")
    return doc


def _extract_sections(filing: Any) -> dict[str, str]:
    """Pull item sections from the filing's structured data object.

    edgartools exposes 10-K/10-Q items via `filing.obj()` (e.g. `TenK`),
    indexable by item name. Items that are missing or trivially short
    (e.g. "Item 4. Mine Safety Disclosures — None.") are skipped.
    """
    report = filing.obj()
    sections: dict[str, str] = {}
    for item in report.items:
        try:
            text = report[item]
        except (KeyError, IndexError):  # pragma: no cover - edgartools quirk guard
            continue
        if not isinstance(text, str):
            continue
        text = text.strip()
        if len(text) >= _MIN_SECTION_CHARS:
            sections[item] = text
    return sections
