"""Section- and table-aware chunking (blueprint §5 — not fixed-window).

Narrative sections split at paragraph boundaries into ~512-1024-token
chunks with ~15% overlap. Tables are atomic chunks — markdown rendering as
the embedded text, the structured original in the payload — split only by
row-groups (headers repeated) when oversized. Every chunk carries the full
citation trail in the Phase 0 `EvidenceChunk` metadata (extended, not
forked): accession_no, filing_date, fiscal_period, item, section_title,
chunk_type, anchor.
"""

import json

from fie.ingest.parse import ParsedFiling, ParsedTable
from fie.state import EvidenceChunk

# ~4 chars per token is a serviceable approximation for filing prose.
CHARS_PER_TOKEN = 4


def chunk_filing(
    filing: ParsedFiling,
    *,
    target_tokens: int = 768,
    overlap_ratio: float = 0.15,
    table_max_tokens: int = 2000,
) -> list[EvidenceChunk]:
    """All chunks for one parsed filing, with stable per-filing anchors."""
    chunks: list[EvidenceChunk] = []
    for section, text in filing.sections.items():
        slices = split_with_overlap(
            text, max_chars=target_tokens * CHARS_PER_TOKEN, overlap_ratio=overlap_ratio
        )
        for i, slice_text in enumerate(slices):
            anchor = f"{_slug(section)}-p{i + 1:03d}"
            chunks.append(
                _chunk(
                    filing,
                    anchor=anchor,
                    section=section,
                    section_title=section,
                    text=slice_text,
                    chunk_type="text",
                    extra={"slice": f"{i + 1}/{len(slices)}"},
                )
            )
    for table in filing.tables:
        chunks.extend(_table_chunks(filing, table, table_max_tokens * CHARS_PER_TOKEN))
    return chunks


def _table_chunks(filing: ParsedFiling, table: ParsedTable, max_chars: int) -> list[EvidenceChunk]:
    """One atomic chunk per table; oversized tables split by row-groups with
    headers repeated so each part stays interpretable."""
    parts = [table]
    if len(table.to_markdown()) > max_chars and len(table.rows) > 1:
        rows_per_part = max(1, len(table.rows) * max_chars // (len(table.to_markdown()) + 1))
        parts = [
            table.model_copy(update={"rows": table.rows[i : i + rows_per_part]})
            for i in range(0, len(table.rows), rows_per_part)
        ]
    chunks: list[EvidenceChunk] = []
    for i, part in enumerate(parts):
        suffix = "" if len(parts) == 1 else f"-r{i + 1:02d}"
        title = part.caption or part.item or "Table"
        context = " — ".join(x for x in (part.item, part.caption) if x)
        chunks.append(
            _chunk(
                filing,
                anchor=f"{part.anchor}{suffix}",
                section=part.item or "Unattributed table",
                section_title=title,
                text=f"{context}\n\n{part.to_markdown()}" if context else part.to_markdown(),
                chunk_type="table",
                extra={
                    # Structured original for display provenance; numeric
                    # correctness comes from XBRL (decision 3.3).
                    "table_json": json.dumps(
                        {"caption": part.caption, "headers": part.headers, "rows": part.rows}
                    ),
                    "slice": f"{i + 1}/{len(parts)}",
                },
            )
        )
    return chunks


def _chunk(
    filing: ParsedFiling,
    *,
    anchor: str,
    section: str,
    section_title: str,
    text: str,
    chunk_type: str,
    extra: dict[str, str],
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"{filing.accession_no}:{anchor}",
        doc_id=f"{filing.ticker}-{filing.form_type}-{filing.accession_no}",
        ticker=filing.ticker,
        form_type=filing.form_type,
        section=section,
        text=text,
        metadata={
            "accession_no": filing.accession_no,
            "filing_date": filing.filing_date,
            "fiscal_period": filing.fiscal_period,
            "fiscal_year": str(filing.fiscal_year or ""),
            "item": section if section.lower().startswith("item") else "",
            "section_title": section_title,
            "chunk_type": chunk_type,
            "anchor": anchor,
            **extra,
        },
    )


def split_with_overlap(text: str, *, max_chars: int, overlap_ratio: float = 0.15) -> list[str]:
    """Split at paragraph boundaries into slices of at most `max_chars`,
    carrying ~`overlap_ratio` of trailing paragraphs into the next slice so
    no claim's context is cut mid-thought. Oversized single paragraphs fall
    back to hard splits (no overlap)."""
    if len(text) <= max_chars:
        return [text]

    paragraphs: list[str] = []
    for para in text.split("\n\n"):
        if not para.strip():
            continue
        if len(para) > max_chars:
            paragraphs.extend(para[i : i + max_chars] for i in range(0, len(para), max_chars))
        else:
            paragraphs.append(para)

    overlap_chars = int(max_chars * overlap_ratio)
    slices: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        if current and size + len(para) + 2 > max_chars:
            slices.append("\n\n".join(current))
            # Seed the next slice with trailing paragraphs up to the overlap.
            tail: list[str] = []
            tail_size = 0
            for prev in reversed(current):
                if tail_size + len(prev) > overlap_chars:
                    break
                tail.insert(0, prev)
                tail_size += len(prev) + 2
            current, size = tail, tail_size
        current.append(para)
        size += len(para) + 2
    if current:
        slices.append("\n\n".join(current))
    return slices


def _slug(section: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in section.lower()).strip("-")
