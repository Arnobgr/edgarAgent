"""EDGAR batch acquisition: enumerate → download → parse → facts.

Extends the Phase 0 edgartools path into the corpus pipeline (workstream
A.1): per ticker, the last N fiscal years of 10-Ks, the 10-Qs and the 8-Ks
filed in that window — 8-Ks filtered to earnings releases (Item 2.02 with
an EX-99 press release). Raw HTML is cached under `data/raw/` (gitignored)
and each filing's manifest row (accession, form, dates, checksum) goes to
SQLite, so re-running skips everything already acquired. SEC rate limiting
is handled inside edgartools (verified: it throttles to 10 req/s and sets
the declared identity).
"""

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fie.ingest.facts import extract_facts
from fie.ingest.parse import (
    ParsedFiling,
    extract_sections,
    extract_tables,
    fiscal_period_label,
    resplit_mangled_sections,
)
from fie.ingest.store import Fact

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AcquiredFiling:
    """One filing after the acquire stage: parse output + provenance."""

    parsed: ParsedFiling
    facts: list[Fact]
    raw_path: str
    checksum: str


def enumerate_filings(ticker: str, years: int) -> list[Any]:
    """The corpus filings for one ticker: `years` 10-Ks, plus 10-Qs and
    earnings 8-Ks filed since the start of that window. Amendments excluded.
    """
    from edgar import Company

    company = Company(ticker)
    tenks = list(company.get_filings(form="10-K", amendments=False).head(years))
    cutoff = _window_start(tenks, years)
    tenqs = list(company.get_filings(form="10-Q", amendments=False, filing_date=f"{cutoff}:"))
    eightks = [
        f
        for f in company.get_filings(form="8-K", amendments=False, filing_date=f"{cutoff}:")
        if "2.02" in (f.items or "")
    ]
    logger.info(
        "%s: %d 10-K, %d 10-Q, %d earnings 8-K since %s",
        ticker,
        len(tenks),
        len(tenqs),
        len(eightks),
        cutoff,
    )
    return [*tenks, *tenqs, *eightks]


def _window_start(tenks: list[Any], years: int) -> str:
    """Start of the corpus window: the filing date of the oldest kept 10-K
    minus a quarter (so the 10-Qs of that fiscal year are included too)."""
    if tenks:
        oldest = min(dt.date.fromisoformat(str(f.filing_date)) for f in tenks)
        return str(oldest - dt.timedelta(days=100))
    return str(dt.date.today() - dt.timedelta(days=int(years * 365.25)))


def acquire_filing(filing: Any, *, ticker: str, raw_dir: Path) -> AcquiredFiling:
    """Download + parse one filing; cache raw HTML under `raw_dir`."""
    form_type = str(filing.form)
    accession_no = str(filing.accession_no)
    filing_dir = raw_dir / ticker.upper() / accession_no
    filing_dir.mkdir(parents=True, exist_ok=True)

    if form_type.startswith("8-K"):
        html, source_name = _press_release_html(filing)
    else:
        html, source_name = str(filing.html() or ""), "primary.html"
    if not html:
        raise RuntimeError(f"no HTML document for {accession_no} ({form_type})")
    raw_path = filing_dir / source_name
    raw_path.write_text(html, errors="ignore")

    fiscal_period, fiscal_year = fiscal_period_label(
        form_type, str(getattr(filing, "period_of_report", "") or "")
    )
    parsed = ParsedFiling(
        ticker=ticker.upper(),
        form_type=form_type,
        accession_no=accession_no,
        filing_date=str(filing.filing_date),
        fiscal_period=fiscal_period,
        fiscal_year=fiscal_year,
        sections=_sections_for(filing, form_type, html),
        tables=extract_tables(html),
    )
    facts = _facts_for(filing, ticker=ticker, accession_no=accession_no, form_type=form_type)
    return AcquiredFiling(
        parsed=parsed,
        facts=facts,
        raw_path=str(raw_path),
        checksum=hashlib.sha256(html.encode()).hexdigest(),
    )


def _press_release_html(filing: Any) -> tuple[str, str]:
    """The EX-99 press release of an earnings 8-K (the analyst-facing part)."""
    for att in filing.attachments:
        if str(getattr(att, "document_type", "")).upper().startswith("EX-99"):
            content = att.download()
            html = content.decode(errors="ignore") if isinstance(content, bytes) else str(content)
            return html, f"ex99-{att.document}"
    raise RuntimeError(f"earnings 8-K {filing.accession_no} has no EX-99 press release")


def _sections_for(filing: Any, form_type: str, html: str) -> dict[str, str]:
    """Narrative sections: item accessors for 10-K/10-Q, whole press-release
    text for 8-K exhibits (they have no item structure)."""
    if form_type.startswith("8-K"):
        from bs4 import BeautifulSoup

        text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
        return {"EX-99 Press Release": text} if text else {}
    sections = resplit_mangled_sections(extract_sections(filing.obj()))
    if not sections:
        raise RuntimeError(f"no sections extracted from {filing.accession_no} ({form_type})")
    return sections


def _facts_for(filing: Any, *, ticker: str, accession_no: str, form_type: str) -> list[Fact]:
    try:
        xbrl = filing.xbrl()
    except Exception as exc:  # XBRL is optional for some 8-Ks
        logger.warning("no XBRL for %s: %s", accession_no, exc)
        return []
    if xbrl is None:
        return []
    return extract_facts(xbrl, ticker=ticker, accession_no=accession_no, form_type=form_type)
