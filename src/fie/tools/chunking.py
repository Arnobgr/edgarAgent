"""Section-aware chunking of a parsed filing into EvidenceChunks.

One chunk per section, or per ~1k-token slice of a section that exceeds the
target size. Splits on paragraph boundaries where possible so slices stay
readable. Every chunk carries the full citation trail in its metadata.
"""

from fie.state import EvidenceChunk
from fie.tools.edgar import FilingDocument

# ~4 chars per token is a serviceable approximation for filing prose.
CHARS_PER_TOKEN = 4
DEFAULT_TARGET_TOKENS = 1000


def chunk_document(
    doc: FilingDocument, target_tokens: int = DEFAULT_TARGET_TOKENS, *, id_prefix: str = ""
) -> list[EvidenceChunk]:
    """Chunk every section of `doc`, assigning sequential citation ids c1..cN.

    `id_prefix` namespaces the ids (e.g. `d2-c1`) so chunks from multiple
    filings can share one corpus without citation-id collisions."""
    max_chars = target_tokens * CHARS_PER_TOKEN
    chunks: list[EvidenceChunk] = []
    for section, text in doc.sections.items():
        slices = _split_text(text, max_chars)
        for i, slice_text in enumerate(slices):
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"{id_prefix}c{len(chunks) + 1}",
                    doc_id=doc.doc_id,
                    ticker=doc.ticker,
                    form_type=doc.form_type,
                    section=section,
                    text=slice_text,
                    metadata={
                        "accession_no": doc.accession_no,
                        "filing_date": doc.filing_date,
                        "slice": f"{i + 1}/{len(slices)}",
                    },
                )
            )
    return chunks


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split text into slices of at most `max_chars`, preferring paragraph
    boundaries and falling back to hard splits for oversized paragraphs."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    slices: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if current:
                slices.append(current)
                current = ""
            slices.extend(para[i : i + max_chars] for i in range(0, len(para), max_chars))
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > max_chars:
            slices.append(current)
            current = para
        else:
            current = candidate
    if current:
        slices.append(current)
    return slices
