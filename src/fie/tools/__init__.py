"""Tool execution: non-LLM actions (filing fetch, chunking, retrieval,
numeric reconciliation).

Phase 0 shipped `fetch_filing` (EDGAR via edgartools, cached under `data/`),
section chunking, and naive in-memory BM25 retrieval. Phase 1 adds interim
numeric reconciliation. Phase 3 adds the XBRL facts store and Qdrant hybrid
retrieval behind the same interfaces.
"""

from fie.tools.chunking import chunk_document
from fie.tools.edgar import FilingDocument, fetch_filing
from fie.tools.reconcile import InterimNumericReconciler, NumericReconciler, ReconcileOutcome
from fie.tools.retrieval import BM25Retriever, Retriever

__all__ = [
    "BM25Retriever",
    "FilingDocument",
    "InterimNumericReconciler",
    "NumericReconciler",
    "ReconcileOutcome",
    "Retriever",
    "chunk_document",
    "fetch_filing",
]
