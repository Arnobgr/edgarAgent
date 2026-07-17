from fie.tools.chunking import CHARS_PER_TOKEN, chunk_document
from fie.tools.edgar import FilingDocument


def make_doc(sections: dict[str, str]) -> FilingDocument:
    return FilingDocument(
        doc_id="AAPL-10-K-0000320193-25-000001",
        ticker="AAPL",
        form_type="10-K",
        accession_no="0000320193-25-000001",
        filing_date="2025-10-31",
        sections=sections,
    )


def test_short_section_becomes_one_chunk_with_metadata() -> None:
    doc = make_doc({"Item 1": "Apple designs smartphones and services." * 5})
    chunks = chunk_document(doc)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "c1"
    assert chunk.doc_id == doc.doc_id
    assert chunk.ticker == "AAPL"
    assert chunk.form_type == "10-K"
    assert chunk.section == "Item 1"
    assert chunk.metadata["accession_no"] == doc.accession_no
    assert chunk.metadata["filing_date"] == doc.filing_date


def test_long_section_is_sliced_with_no_text_lost() -> None:
    paragraphs = [f"Paragraph {i}: " + ("revenue grew steadily. " * 40) for i in range(20)]
    text = "\n\n".join(paragraphs)
    doc = make_doc({"Item 7": text})

    chunks = chunk_document(doc, target_tokens=500)
    max_chars = 500 * CHARS_PER_TOKEN

    assert len(chunks) > 1
    assert all(len(c.text) <= max_chars for c in chunks)
    assert all(c.section == "Item 7" for c in chunks)
    # No paragraph was dropped.
    rejoined = "\n\n".join(c.text for c in chunks)
    for para in paragraphs:
        assert para in rejoined
    # Slice metadata is consistent.
    assert chunks[0].metadata["slice"] == f"1/{len(chunks)}"


def test_chunk_ids_are_sequential_across_sections() -> None:
    doc = make_doc(
        {
            "Item 1": "Business description. " * 30,
            "Item 1A": "Risk factors. " * 30,
            "Item 7": "MD&A. " * 30,
        }
    )
    chunks = chunk_document(doc)

    assert [c.chunk_id for c in chunks] == [f"c{i + 1}" for i in range(len(chunks))]
    assert {c.section for c in chunks} == {"Item 1", "Item 1A", "Item 7"}
