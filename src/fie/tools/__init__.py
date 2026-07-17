"""Tool execution: non-LLM actions (filing fetch, chunking, retrieval).

Phase 0 ships `fetch_filing` (EDGAR via edgartools, cached under `data/`),
section chunking, and naive in-memory BM25 retrieval. Phase 3 adds the XBRL
facts store, Qdrant hybrid retrieval, and reconciliation.
"""

from fie.tools.chunking import chunk_document
from fie.tools.edgar import FilingDocument, fetch_filing
from fie.tools.retrieval import BM25Retriever, Retriever

__all__ = [
    "BM25Retriever",
    "FilingDocument",
    "Retriever",
    "chunk_document",
    "fetch_filing",
]
