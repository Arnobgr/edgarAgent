"""The ingestion pipeline (workstream A.6): fetch → parse → chunk → embed →
upsert, resumable per filing, with a summary report.

Three stages, tracked per accession in the manifest:

- **acquire** (network): enumerate + download + structure-aware parse +
  XBRL facts → `data/parsed/<accession>.json` + facts in SQLite.
- **chunk** (local): parsed filing → EvidenceChunks → `data/chunks/*.jsonl`.
- **embed** (API + Qdrant): dense + sparse vectors → upsert. Skipped (and
  reported) when the embedding key or Qdrant is unavailable, so the free
  stages still make progress.

Re-running skips completed stages; failures isolate to their filing.
"""

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel

from fie.config import Settings
from fie.ingest.chunk import chunk_filing
from fie.ingest.fetch import acquire_filing, enumerate_filings
from fie.ingest.parse import ParsedFiling
from fie.ingest.store import (
    STATUS_ACQUIRED,
    STATUS_CHUNKED,
    STATUS_EMBEDDED,
    STATUS_FAILED,
    IngestStore,
    ManifestRow,
)
from fie.state import EvidenceChunk

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    """Summary of one pipeline run (printed by the CLI, asserted in tests)."""

    filings_seen: int = 0
    acquired: int = 0
    skipped: int = 0
    chunked: int = 0
    embedded: int = 0
    total_chunks: int = 0
    total_tables: int = 0
    total_facts: int = 0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"filings: {self.filings_seen} seen, {self.acquired} newly acquired, "
            f"{self.skipped} already cached",
            f"chunked: {self.chunked} filing(s) → {self.total_chunks} chunks "
            f"({self.total_tables} tables)",
            f"facts: {self.total_facts} XBRL facts in store",
            f"embedded: {self.embedded} filing(s) upserted to Qdrant",
        ]
        out.extend(f"note: {n}" for n in self.notes)
        out.extend(f"FAILED: {f}" for f in self.failures)
        return out


class _ChunkFile(BaseModel):
    chunks: list[EvidenceChunk]


def run_ingest(
    settings: Settings,
    *,
    tickers: list[str] | None = None,
    years: int | None = None,
    embedding_provider: str | None = None,
    skip_embed: bool = False,
) -> IngestReport:
    """Run all stages for the corpus. Scope is config, not code."""
    tickers = tickers or settings.corpus_tickers
    years = years or settings.ingest_years
    store = IngestStore(settings.ingest_db_path)
    report = IngestReport()
    try:
        _stage_acquire(settings, store, report, tickers, years)
        _stage_chunk(settings, store, report)
        if skip_embed:
            report.notes.append("embed stage skipped (--skip-embed)")
        else:
            _stage_embed(settings, store, report, embedding_provider)
        _stamp_totals(store, report)
    finally:
        store.close()
    return report


def _stage_acquire(
    settings: Settings,
    store: IngestStore,
    report: IngestReport,
    tickers: list[str],
    years: int,
) -> None:
    from edgar import set_identity

    if not settings.edgar_identity.strip():
        raise RuntimeError(
            "EDGAR_IDENTITY is not set — add it to .env as "
            '"Firstname Lastname email@example.com" (SEC requires it)'
        )
    set_identity(settings.edgar_identity)
    raw_dir = settings.data_dir / "raw"
    parsed_dir = settings.data_dir / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        filings = enumerate_filings(ticker, years)
        report.filings_seen += len(filings)
        for filing in filings:
            accession_no = str(filing.accession_no)
            existing = store.get_manifest(accession_no)
            if existing is not None and existing.status != STATUS_FAILED:
                report.skipped += 1
                continue
            try:
                acquired = acquire_filing(filing, ticker=ticker, raw_dir=raw_dir)
            except Exception as exc:
                logger.warning("acquire failed for %s: %s", accession_no, exc)
                report.failures.append(f"acquire {ticker} {accession_no}: {exc}")
                store.upsert_manifest(
                    ManifestRow(
                        accession_no=accession_no,
                        ticker=ticker.upper(),
                        form_type=str(filing.form),
                        filing_date=str(filing.filing_date),
                        status=STATUS_FAILED,
                        detail=str(exc),
                    )
                )
                continue
            parsed_path = parsed_dir / f"{accession_no}.json"
            parsed_path.write_text(acquired.parsed.model_dump_json())
            n_facts = store.replace_facts(accession_no, acquired.facts)
            store.upsert_manifest(
                ManifestRow(
                    accession_no=accession_no,
                    ticker=ticker.upper(),
                    form_type=acquired.parsed.form_type,
                    filing_date=acquired.parsed.filing_date,
                    period_of_report=str(getattr(filing, "period_of_report", "") or ""),
                    fiscal_period=acquired.parsed.fiscal_period,
                    checksum=acquired.checksum,
                    raw_path=acquired.raw_path,
                    status=STATUS_ACQUIRED,
                    n_sections=len(acquired.parsed.sections),
                    n_tables=len(acquired.parsed.tables),
                    n_facts=n_facts,
                )
            )
            report.acquired += 1
            logger.info(
                "acquired %s %s (%d sections, %d tables, %d facts)",
                ticker,
                accession_no,
                len(acquired.parsed.sections),
                len(acquired.parsed.tables),
                n_facts,
            )


def _stage_chunk(settings: Settings, store: IngestStore, report: IngestReport) -> None:
    chunks_dir = settings.data_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for row in store.list_manifest(STATUS_ACQUIRED):
        parsed_path = settings.data_dir / "parsed" / f"{row.accession_no}.json"
        try:
            filing = ParsedFiling.model_validate_json(parsed_path.read_text())
            chunks = chunk_filing(
                filing,
                target_tokens=settings.chunk_target_tokens,
                overlap_ratio=settings.chunk_overlap_ratio,
                table_max_tokens=settings.table_max_tokens,
            )
        except Exception as exc:
            report.failures.append(f"chunk {row.accession_no}: {exc}")
            store.set_status(row.accession_no, STATUS_FAILED, f"chunk: {exc}")
            continue
        (chunks_dir / f"{row.accession_no}.json").write_text(
            _ChunkFile(chunks=chunks).model_dump_json()
        )
        store.set_counts(row.accession_no, n_chunks=len(chunks))
        store.set_status(row.accession_no, STATUS_CHUNKED)
        report.chunked += 1


def _stage_embed(
    settings: Settings,
    store: IngestStore,
    report: IngestReport,
    embedding_provider: str | None,
) -> None:
    provider = embedding_provider or settings.embedding_provider
    key = {
        "voyage": settings.voyage_api_key,
        "openai": settings.openai_api_key,
    }.get(provider)
    if key is None or not key.get_secret_value():
        report.notes.append(
            f"embed stage skipped: no API key for embedding provider {provider!r} "
            "(add VOYAGE_API_KEY / OPENAI_API_KEY to .env and re-run — "
            "acquire/chunk progress is kept)"
        )
        return

    from qdrant_client import QdrantClient

    from fie.ingest.embed import get_embedder
    from fie.tools.retrieval import Bm25SparseEncoder, ensure_collection, upsert_chunks

    client = QdrantClient(url=settings.qdrant_url)
    embedder = get_embedder(settings, provider)
    sparse = Bm25SparseEncoder()
    collection = settings.qdrant_collection(provider)
    ensure_collection(client, collection, embedder.dim)

    for row in store.list_manifest(STATUS_CHUNKED):
        chunk_path = settings.data_dir / "chunks" / f"{row.accession_no}.json"
        try:
            chunks = _ChunkFile.model_validate_json(chunk_path.read_text()).chunks
            texts = [c.text for c in chunks]
            upsert_chunks(
                client,
                collection,
                chunks,
                embedder.embed(texts, input_type="document"),
                sparse.encode_documents(texts),
            )
        except Exception as exc:
            report.failures.append(f"embed {row.accession_no}: {exc}")
            store.set_status(row.accession_no, STATUS_FAILED, f"embed: {exc}")
            continue
        store.set_status(row.accession_no, STATUS_EMBEDDED)
        report.embedded += 1


def _stamp_totals(store: IngestStore, report: IngestReport) -> None:
    rows = store.list_manifest()
    report.total_chunks = sum(r.n_chunks for r in rows)
    report.total_tables = sum(r.n_tables for r in rows)
    report.total_facts = store.count_facts()
