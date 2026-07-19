"""Tool execution: non-LLM actions (filing fetch, chunking, retrieval,
numeric reconciliation).

Phase 0 shipped `fetch_filing` (EDGAR via edgartools, cached under `data/`)
and section chunking. Phase 1 added interim numeric reconciliation. Phase 3
replaced the in-memory BM25 placeholder with Qdrant hybrid retrieval and
made XBRL-exact reconciliation the default — both behind the same
protocols, so the graph never changed.
"""

from fie.tools.chunking import chunk_document
from fie.tools.edgar import FilingDocument, fetch_filing, fetch_recent_filings
from fie.tools.reconcile import (
    InterimNumericReconciler,
    NumericReconciler,
    ReconcileOutcome,
    XbrlNumericReconciler,
)
from fie.tools.retrieval import QdrantHybridRetriever, Retriever

__all__ = [
    "FilingDocument",
    "InterimNumericReconciler",
    "NumericReconciler",
    "QdrantHybridRetriever",
    "ReconcileOutcome",
    "Retriever",
    "XbrlNumericReconciler",
    "chunk_document",
    "fetch_filing",
    "fetch_recent_filings",
]
