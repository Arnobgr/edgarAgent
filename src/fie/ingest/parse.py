"""Structure-aware filing parsing (blueprint §5, the hard part).

Narrative comes from edgartools' item/section accessors (validated on TSLA,
the least standard filer of the three — see ADR 0010). Tables are **never
flattened to prose**: edgartools' item text drops table structure, so tables
are extracted separately from the filing HTML with BeautifulSoup — each one
keeps its structured rows/cols/headers (chunk payload) plus a markdown
rendering (for embedding), its caption, and the containing item, attributed
by document position (lxml sourceline) relative to item headings.

8-K earnings filings are parsed as the EX-99 press-release attachment text
plus its tables.
"""

import re
from typing import Any

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

# Item headings look like "Item 7." / "ITEM 1A:" — short, at block level.
_ITEM_RE = re.compile(r"^\s*item\s+(\d{1,2}[a-c]?)\s*[.:—-]?\s", re.IGNORECASE)
_MAX_HEADING_CHARS = 150
# Tables with no digits at all are layout/TOC artifacts, not data.
_DIGIT_RE = re.compile(r"\d")
_MIN_SECTION_CHARS = 200


class ParsedTable(BaseModel):
    """One extracted table: structured cells + citation context."""

    anchor: str  # stable within the filing, e.g. "table-0007"
    item: str  # containing item ("Item 8") or "" when unattributed
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Pipe-table rendering used as the table chunk's embedded text."""
        lines: list[str] = []
        if self.headers:
            lines.append("| " + " | ".join(self.headers) + " |")
            lines.append("|" + "---|" * len(self.headers))
        lines.extend("| " + " | ".join(row) + " |" for row in self.rows)
        return "\n".join(lines)


class ParsedFiling(BaseModel):
    """The parse output for one filing: sections, tables, citation metadata."""

    ticker: str
    form_type: str
    accession_no: str
    filing_date: str
    fiscal_period: str  # e.g. "FY2025" or "Q2 2026"
    fiscal_year: int | None = None
    sections: dict[str, str] = Field(default_factory=dict)
    tables: list[ParsedTable] = Field(default_factory=list)


def extract_tables(html: str) -> list[ParsedTable]:
    """Extract data tables from filing HTML, attributed to their items.

    Attribution walks the document in source order: the most recent item
    heading before a table owns it. Layout tables (no digits, or fewer than
    two rows/columns after cleaning) are dropped.
    """
    soup = BeautifulSoup(html, "lxml")
    current_item = ""
    tables: list[ParsedTable] = []
    for element in soup.find_all(["p", "div", "span", "b", "h1", "h2", "h3", "h4", "table"]):
        if element.name == "table":
            if element.find("table") is not None:  # keep innermost tables only
                continue
            parsed = _parse_table(element, len(tables), current_item)
            if parsed is not None:
                tables.append(parsed)
            continue
        # Heading candidate: short block text starting with "Item N".
        if element.find("table") is not None:
            continue
        text = element.get_text(" ", strip=True)
        if 0 < len(text) <= _MAX_HEADING_CHARS:
            match = _ITEM_RE.match(text + " ")
            if match:
                current_item = f"Item {match.group(1).upper()}"
    return tables


def _parse_table(table: Tag, index: int, item: str) -> ParsedTable | None:
    grid: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if any(cells):
            grid.append(cells)
    grid = _drop_empty_columns(grid)
    if len(grid) < 2 or max((len(r) for r in grid), default=0) < 2:
        return None
    if not any(_DIGIT_RE.search(cell) for row in grid for cell in row):
        return None

    # The first row is a header when all its non-empty cells are labels —
    # prose, years ("2025"), or period phrases ("December 31, 2025") — as
    # opposed to data magnitudes.
    headers: list[str] = []
    rows = grid
    first = grid[0]
    if all(_is_header_cell(c) for c in first if c):
        headers, rows = first, grid[1:]
    return ParsedTable(
        anchor=f"table-{index:04d}",
        item=item,
        caption=_find_caption(table),
        headers=headers,
        rows=rows,
    )


_HEADER_NUM_RE = re.compile(r"^(?:fy\s*)?(?:19|20)\d{2}$", re.IGNORECASE)


def _is_header_cell(cell: str) -> bool:
    if not _DIGIT_RE.search(cell):
        return True
    # Years and period phrases ("2025", "FY2025", "December 31, 2025").
    return bool(_HEADER_NUM_RE.match(cell.strip())) or bool(
        re.search(r"(?:19|20)\d{2}", cell) and re.search(r"[a-zA-Z]", cell)
    )


def _drop_empty_columns(grid: list[list[str]]) -> list[list[str]]:
    """Filing HTML pads tables with empty layout columns; remove any column
    that is empty in every row (ragged rows are padded first)."""
    if not grid:
        return grid
    width = max(len(r) for r in grid)
    padded = [r + [""] * (width - len(r)) for r in grid]
    keep = [i for i in range(width) if any(row[i].strip() for row in padded)]
    cleaned = [[row[i] for i in keep] for row in padded]
    return [row for row in cleaned if any(cell.strip() for cell in row)]


def _find_caption(table: Tag) -> str:
    """The nearest short preceding text block — usually the table's title."""
    element: Any = table
    for _ in range(5):
        element = element.find_previous_sibling()
        if element is None:
            break
        if not isinstance(element, Tag):
            continue
        text = element.get_text(" ", strip=True)
        if text and len(text) <= _MAX_HEADING_CHARS and not _ITEM_RE.match(text + " "):
            return text
        if len(text) > _MAX_HEADING_CHARS:
            break
    return ""


def extract_sections(report: Any) -> dict[str, str]:
    """Item sections from an edgartools report object (TenK/TenQ).

    Items that are missing or trivially short ("None.") are skipped.
    Table content inside items is ignored here — tables come from
    `extract_tables`, with structure intact.
    """
    sections: dict[str, str] = {}
    for item in report.items:
        try:
            text = report[item]
        except (KeyError, IndexError):  # pragma: no cover - edgartools quirk guard
            continue
        if isinstance(text, str) and len(text.strip()) >= _MIN_SECTION_CHARS:
            sections[item] = text.strip()
    return sections


# In-text item headings inside a mis-segmented blob: "ITEM 2. MANAGEMENT'S
# DISCUSSION…" on its own line, title in (mostly) capitals.
_INLINE_ITEM_RE = re.compile(r"(?m)^[ \t]{0,8}ITEM[ \t]+(\d{1,2}[A-Ca-c]?)[ \t]*[.:][^\n]*$")


def resplit_mangled_sections(sections: dict[str, str]) -> dict[str, str]:
    """Fallback for edgartools' 10-Q segmentation (ADR 0010): on TSLA it
    returns overlapping blobs under wrong item labels (e.g. all of Part I
    inside "Part I, Item 4"). Sections containing two or more in-text ITEM
    headings are re-split at those headings — tracking the Part I → Part II
    transition by item numbers restarting — and the pieces are merged
    across blobs, keeping the longest text per item. Sections with at most
    one heading were segmented fine and keep their original label.

    Measured under edgartools 5.61.1 (ADR 0048): it fires on no corpus
    filing once `drop_duplicated_items` has run first. Before that, it fired
    only on TSLA's FY2024/FY2025 10-Ks, whose "Item 1B" also carried Item 1C,
    and added a duplicate "Part I, Item 1C"."""
    if not any(len(_INLINE_ITEM_RE.findall(text)) >= 2 for text in sections.values()):
        return sections
    merged: dict[str, str] = {}
    passthrough: dict[str, str] = {}
    for original_label, text in sections.items():
        if len(_INLINE_ITEM_RE.findall(text)) < 2:
            passthrough[original_label] = text
            continue
        for label, segment in _split_blob(text):
            if len(segment.strip()) >= _MIN_SECTION_CHARS and len(segment) > len(
                merged.get(label, "")
            ):
                merged[label] = segment.strip()
    if not merged:
        return sections
    # Re-split labels win over pass-through duplicates of the same item.
    return {**passthrough, **merged}


def drop_duplicated_items(sections: dict[str, str]) -> dict[str, str]:
    """Remove the two duplications edgartools 5.61.1 produces on the corpus (ADR 0048).

    A 10-K item can run on past the next item's heading. TSLA's FY2024 and
    FY2025 "Item 1B" carry all of Item 1C, which edgartools also returns on
    its own, so the section is cut at that heading. 10-Q labels ("Part I,
    Item 1") never equal a bare "Item 1C", so only 10-K items are cut here.
    A mangled 10-Q blob is left to `resplit_mangled_sections`.

    One text can also come back under two labels. TSLA's Q3 2023 10-Q returns
    Legal Proceedings as both "Part I, Item 1" and "Part II, Item 1". The
    later label is kept, because in the observed case the earlier item could
    not be anchored and took the next one's text. That rests on one case.
    """
    trimmed: dict[str, str] = {}
    for label, text in sections.items():
        for match in _INLINE_ITEM_RE.finditer(text):
            other = f"Item {match.group(1).upper()}"
            if other != label and other in sections and text[: match.start()].strip():
                text = text[: match.start()].strip()
                break
        if len(text) >= _MIN_SECTION_CHARS:
            trimmed[label] = text
    last_label = {text: label for label, text in trimmed.items()}
    return {label: text for label, text in trimmed.items() if last_label[text] == label}


def _split_blob(text: str) -> list[tuple[str, str]]:
    matches = list(_INLINE_ITEM_RE.finditer(text))
    if not matches:
        return []
    pieces: list[tuple[str, str]] = []
    part = "Part I"
    last_number = 0
    # A 10-Q's Part I starts with Item 1 (financial statements) — content
    # before the first detected heading belongs to it.
    if matches[0].start() > 0 and _item_number(matches[0].group(1)) >= 2:
        pieces.append(("Part I, Item 1", text[: matches[0].start()]))
        last_number = 1
    for i, match in enumerate(matches):
        number = _item_number(match.group(1))
        if number < last_number:
            part = "Part II"
        last_number = number
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pieces.append((f"{part}, Item {match.group(1).upper()}", text[match.start() : end]))
    return pieces


def _item_number(item: str) -> int:
    return int(re.sub(r"[^0-9]", "", item) or 0)


def fiscal_period_label(form_type: str, period_of_report: str | None) -> tuple[str, int | None]:
    """A human-stable fiscal-period label + year from the period of report.

    10-K → "FY2025"; 10-Q/8-K → "Q2 2026" by calendar quarter of the period
    end (an approximation for off-calendar fiscal years like AAPL's; the
    facts table carries exact XBRL periods — this label exists for filtering
    and citation display).
    """
    if not period_of_report:
        return "", None
    match = re.match(r"(\d{4})-(\d{2})", str(period_of_report))
    if not match:
        return "", None
    year, month = int(match.group(1)), int(match.group(2))
    if form_type.startswith("10-K"):
        return f"FY{year}", year
    quarter = (month - 1) // 3 + 1
    return f"Q{quarter} {year}", year
