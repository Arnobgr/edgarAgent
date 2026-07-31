"""SQLite ingestion store: the filing manifest and the XBRL facts table.

One database file (`data/ingest.db`) holds both tables. The manifest makes
the pipeline idempotent and resumable — each filing carries a stage status
(`acquired` → `chunked` → `embedded`, or `failed`) and re-running skips
completed stages. The facts table is the numeric ground truth (decision
3.3): verification and gold-answer construction both read it; parsed HTML
cells are display-only provenance.
"""

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Stage progression; `failed` is re-attempted on the next run.
STATUS_ACQUIRED = "acquired"
STATUS_CHUNKED = "chunked"
STATUS_EMBEDDED = "embedded"
STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession_no TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    form_type TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    period_of_report TEXT,
    fiscal_period TEXT,
    checksum TEXT,
    raw_path TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    n_sections INTEGER DEFAULT 0,
    n_tables INTEGER DEFAULT 0,
    n_chunks INTEGER DEFAULT 0,
    n_facts INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS facts (
    ticker TEXT NOT NULL,
    concept TEXT NOT NULL,
    label TEXT,
    value REAL NOT NULL,
    unit TEXT,
    decimals INTEGER,
    period_start TEXT,
    period_end TEXT,
    fiscal_period TEXT,
    fiscal_year INTEGER,
    accession_no TEXT NOT NULL,
    form_type TEXT,
    dimensions TEXT NOT NULL DEFAULT '',
    -- `label` is in the key because this edgartools version collapses
    -- dimensions to a flag: distinct axis members (Products/Services/
    -- iPhone...) share concept, period and the flag, and only the label
    -- tells them apart. Without it, INSERT OR IGNORE silently deleted
    -- every disaggregation row after the first (ADR 0019).
    UNIQUE (accession_no, concept, label, unit, period_start, period_end, dimensions)
);
CREATE INDEX IF NOT EXISTS idx_facts_concept ON facts (concept);
CREATE INDEX IF NOT EXISTS idx_facts_ticker_period ON facts (ticker, fiscal_year, fiscal_period);
CREATE INDEX IF NOT EXISTS idx_facts_value ON facts (value);
"""


# How one `concept_terms` entry matches a fact. The first three directions are
# term-inside-field. The fourth is field-inside-term, and it is what lets a
# mapper's compound vocabulary reach a shorter filed label: `%ProductsRevenue%`
# against a label of `Products` is unsatisfiable by construction, so any term
# longer than the label it names could never match, and g-013's
# services-vs-products growth comparison was ruled unsupported on exactly that
# (ADR 0021). The length floor stops generic labels — "Total", "Other", "Net",
# "Cost" — from matching every compound term an analyst could write.
_MIN_LABEL_MATCH = 6
_CONCEPT_MATCH = (
    "(concept LIKE ? OR label LIKE ? OR REPLACE(label, ' ', '') LIKE ?"
    " OR (LENGTH(REPLACE(label, ' ', '')) >= ?"
    " AND REPLACE(?, ' ', '') LIKE '%' || REPLACE(label, ' ', '') || '%'))"
)


@dataclass(frozen=True)
class ManifestRow:
    """One filing's manifest entry."""

    accession_no: str
    ticker: str
    form_type: str
    filing_date: str
    period_of_report: str | None = None
    fiscal_period: str | None = None
    checksum: str | None = None
    raw_path: str | None = None
    status: str = STATUS_ACQUIRED
    detail: str | None = None
    n_sections: int = 0
    n_tables: int = 0
    n_chunks: int = 0
    n_facts: int = 0


@dataclass(frozen=True)
class Fact:
    """One XBRL fact, normalized on ingest (value is the absolute number)."""

    ticker: str
    concept: str
    label: str | None
    value: float
    unit: str | None
    decimals: int | None
    period_start: str | None
    period_end: str | None
    fiscal_period: str | None
    fiscal_year: int | None
    accession_no: str
    form_type: str | None
    dimensions: dict[str, str] | None = None


class IngestStore:
    """Manifest + facts store over one SQLite file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The reconciler reads from graph worker threads (asyncio.to_thread
        # in the verify node), so the connection must cross threads. CPython
        # ships SQLite in serialized mode (threadsafety 3) which makes the
        # shared connection safe; fail loudly rather than corrupt if not.
        if sqlite3.threadsafety < 3:  # pragma: no cover - build-dependent
            raise RuntimeError("SQLite build is not serialized-mode; cannot share connections")
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Serialized mode protects individual calls, but not close() racing
        # an in-flight query: a timed-out verify branch's to_thread cannot
        # be cancelled, so its reconcile keeps querying after the graph
        # aborts, and the run teardown's close() then hit the C layer mid-
        # statement — the SQLITE_MISUSE/segfault that killed one question
        # in each of the three 2026-07-28 eval runs (ADR 0019). The lock
        # makes close() wait out in-flight queries; the flag turns orphaned
        # late queries into a clean error instead of a native crash.
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._conn.close()

    # --- Manifest ---

    def upsert_manifest(self, row: ManifestRow) -> None:
        self._conn.execute(
            """
            INSERT INTO filings (accession_no, ticker, form_type, filing_date,
                period_of_report, fiscal_period, checksum, raw_path, status,
                detail, n_sections, n_tables, n_chunks, n_facts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (accession_no) DO UPDATE SET
                fiscal_period = excluded.fiscal_period,
                checksum = excluded.checksum,
                raw_path = excluded.raw_path,
                status = excluded.status,
                detail = excluded.detail,
                n_sections = excluded.n_sections,
                n_tables = excluded.n_tables,
                n_chunks = excluded.n_chunks,
                n_facts = excluded.n_facts
            """,
            (
                row.accession_no,
                row.ticker,
                row.form_type,
                row.filing_date,
                row.period_of_report,
                row.fiscal_period,
                row.checksum,
                row.raw_path,
                row.status,
                row.detail,
                row.n_sections,
                row.n_tables,
                row.n_chunks,
                row.n_facts,
            ),
        )
        self._conn.commit()

    def set_status(self, accession_no: str, status: str, detail: str | None = None) -> None:
        self._conn.execute(
            "UPDATE filings SET status = ?, detail = ? WHERE accession_no = ?",
            (status, detail, accession_no),
        )
        self._conn.commit()

    def set_counts(self, accession_no: str, **counts: int) -> None:
        """Update any of n_sections / n_tables / n_chunks / n_facts."""
        allowed = {"n_sections", "n_tables", "n_chunks", "n_facts"}
        unknown = set(counts) - allowed
        if unknown:
            raise ValueError(f"unknown count column(s): {sorted(unknown)}")
        sets = ", ".join(f"{k} = ?" for k in counts)
        self._conn.execute(
            f"UPDATE filings SET {sets} WHERE accession_no = ?",
            (*counts.values(), accession_no),
        )
        self._conn.commit()

    def get_manifest(self, accession_no: str) -> ManifestRow | None:
        cur = self._conn.execute("SELECT * FROM filings WHERE accession_no = ?", (accession_no,))
        row = cur.fetchone()
        return _to_manifest(row) if row else None

    def list_manifest(self, status: str | None = None) -> list[ManifestRow]:
        if status is None:
            cur = self._conn.execute("SELECT * FROM filings ORDER BY ticker, filing_date")
        else:
            cur = self._conn.execute(
                "SELECT * FROM filings WHERE status = ? ORDER BY ticker, filing_date", (status,)
            )
        return [_to_manifest(r) for r in cur.fetchall()]

    # --- Facts ---

    def replace_facts(self, accession_no: str, facts: Iterable[Fact]) -> int:
        """Replace all facts for one filing (idempotent re-parse)."""
        self._conn.execute("DELETE FROM facts WHERE accession_no = ?", (accession_no,))
        n = 0
        for f in facts:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO facts (ticker, concept, label, value, unit,
                    decimals, period_start, period_end, fiscal_period, fiscal_year,
                    accession_no, form_type, dimensions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f.ticker,
                    f.concept,
                    f.label,
                    f.value,
                    f.unit,
                    f.decimals,
                    f.period_start,
                    f.period_end,
                    f.fiscal_period,
                    f.fiscal_year,
                    f.accession_no,
                    f.form_type,
                    json.dumps(f.dimensions, sort_keys=True) if f.dimensions else "",
                ),
            )
            n += 1
        self._conn.commit()
        return n

    def query_facts(
        self,
        *,
        tickers: list[str] | None = None,
        concept_terms: list[str] | None = None,
        fiscal_year: int | None = None,
        fiscal_period: str | None = None,
        period_end: str | None = None,
        unit: str | None = None,
        include_dimensioned: bool = True,
        limit: int = 200,
    ) -> list[Fact]:
        """Deterministic fact lookup for reconciliation and gold answers.

        `concept_terms` match the concept or label case-insensitively as
        substrings (any term); labels are also compared space-stripped, so a
        mapper's camel-case vocabulary ("NetSales") reaches a label filed
        with spaces ("Net sales") — this gap alone kept four ratio claims
        ungrounded in the 2026-07-27 re-baseline (ADR 0018). Non-dimensioned
        facts sort first — headline totals beat segment breakdowns when both
        match.

        `period_end` pins one balance date. A fiscal year holds four of them
        for a balance-sheet concept, so the year alone cannot name the figure
        "at year end" means; the gold labels use this to resolve to exactly
        one value (ADR 0020).
        """
        where: list[str] = []
        params: list[object] = []
        if tickers:
            where.append(f"ticker IN ({','.join('?' * len(tickers))})")
            params.extend(t.upper() for t in tickers)
        if concept_terms:
            ors = " OR ".join(_CONCEPT_MATCH for _ in concept_terms)
            where.append(f"({ors})")
            for term in concept_terms:
                like = f"%{term}%"
                params.extend([like, like, f"%{term.replace(' ', '')}%", _MIN_LABEL_MATCH, term])
        if fiscal_year is not None:
            where.append("fiscal_year = ?")
            params.append(fiscal_year)
        if fiscal_period is not None:
            where.append("fiscal_period = ?")
            params.append(fiscal_period)
        if period_end is not None:
            where.append("period_end = ?")
            params.append(period_end)
        if unit is not None:
            where.append("LOWER(unit) = LOWER(?)")
            params.append(unit)
        if not include_dimensioned:
            where.append("dimensions = ''")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("IngestStore is closed (orphaned late query)")
            cur = self._conn.execute(
                f"SELECT * FROM facts {clause} ORDER BY dimensions != '', concept LIMIT ?",
                (*params, limit),
            )
            return [_to_fact(r) for r in cur.fetchall()]

    def count_facts(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM facts")
        return int(cur.fetchone()[0])


def _to_manifest(row: sqlite3.Row) -> ManifestRow:
    return ManifestRow(**{k: row[k] for k in row.keys()})  # noqa: SIM118


def _to_fact(row: sqlite3.Row) -> Fact:
    data = {k: row[k] for k in row.keys()}  # noqa: SIM118
    raw_dims = data.pop("dimensions", "")
    return Fact(**data, dimensions=json.loads(raw_dims) if raw_dims else None)
