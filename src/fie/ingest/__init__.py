"""Production ingestion (Phase 3, workstream A): EDGAR batch fetch,
structure-aware parsing (tables never flattened), section/table-aware
chunking, the XBRL facts store, embeddings, and the resumable pipeline.
"""

from fie.ingest.chunk import chunk_filing, split_with_overlap
from fie.ingest.embed import Embedder, OpenAIEmbedder, VoyageEmbedder, get_embedder
from fie.ingest.facts import extract_facts
from fie.ingest.parse import ParsedFiling, ParsedTable, extract_tables, fiscal_period_label
from fie.ingest.pipeline import IngestReport, run_ingest
from fie.ingest.store import Fact, IngestStore, ManifestRow

__all__ = [
    "Embedder",
    "Fact",
    "IngestReport",
    "IngestStore",
    "ManifestRow",
    "OpenAIEmbedder",
    "ParsedFiling",
    "ParsedTable",
    "VoyageEmbedder",
    "chunk_filing",
    "extract_facts",
    "extract_tables",
    "fiscal_period_label",
    "get_embedder",
    "run_ingest",
    "split_with_overlap",
]
