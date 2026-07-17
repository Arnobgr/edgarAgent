"""Context management: what goes into each LLM call's window.

Packing decisions (which chunks, how truncated, how formatted) live here —
never inside graph nodes (blueprint §3: the separation is the point).
"""

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from fie.state import EvidenceChunk

# Cap on evidence text per chunk when packed into the window. Generous for
# Phase 0's single filing; retrieval-as-context does the real work later.
MAX_CHARS_PER_CHUNK = 6000

SYNTHESIS_SYSTEM_PROMPT = """\
You are a financial analyst answering questions about a company using only \
the evidence excerpts provided from its SEC filings.

Rules:
- Base every factual claim strictly on the evidence provided. If the \
evidence does not support an answer, say so explicitly.
- Cite the supporting chunk id inline after each claim, like [c3]. Every \
figure and material claim needs a citation.
- Be precise with numbers, fiscal periods, and segment names exactly as \
they appear in the evidence.
"""


def pack_evidence(
    evidence: list[EvidenceChunk], max_chars_per_chunk: int = MAX_CHARS_PER_CHUNK
) -> str:
    """Format evidence chunks for the model, one block per chunk, each headed
    by its citation id and source (ticker, form, section, filing date)."""
    blocks: list[str] = []
    for chunk in evidence:
        text = chunk.text
        if len(text) > max_chars_per_chunk:
            text = text[:max_chars_per_chunk] + " …[truncated]"
        filing_date = chunk.metadata.get("filing_date", "unknown date")
        header = (
            f"[{chunk.chunk_id}] {chunk.ticker} {chunk.form_type} ({filing_date}) — {chunk.section}"
        )
        blocks.append(f"{header}\n{text}")
    return "\n\n---\n\n".join(blocks)


def build_synthesis_messages(question: str, evidence: list[EvidenceChunk]) -> list[BaseMessage]:
    """The full message list for the synthesis call."""
    return [
        SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Question: {question}\n\nEvidence from SEC filings:\n\n{pack_evidence(evidence)}"
            )
        ),
    ]
