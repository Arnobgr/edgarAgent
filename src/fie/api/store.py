"""SQLite state for the HTTP service: jobs, the spend ledger, the per-IP
quota and the answer cache.

One file (`data/api.db`), deliberately separate from `ingest.db` (read-mostly
corpus truth) and `checkpoints.db` (LangGraph's, and the one `fie
export-runs` reads): serving state can then be pruned, backed up or dropped
without putting either of the other two at risk.

Every query here is a single indexed row, so the handlers call them inline
rather than through `asyncio.to_thread` — the thread hop would cost more than
the query. The connection is shared across the event loop and the background
job tasks under one lock, the same arrangement `IngestStore` uses and for the
same reason (ADR 0019).
"""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

JobStatus = Literal["queued", "running", "awaiting_review", "succeeded", "failed", "interrupted"]
# Statuses a job can still leave under its own power. A process death strands
# exactly these, which is what `recover_orphans` looks for on boot.
LIVE_STATUSES: tuple[JobStatus, ...] = ("queued", "running")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    question TEXT NOT NULL,
    companies TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- The RunResult contract, verbatim. Written once the graph returns,
    -- including when it returns paused at the review gate.
    result_json TEXT,
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    tokens INTEGER NOT NULL DEFAULT 0,
    -- 1 when the answer came from the cache rather than from a run of this
    -- job's own. The RunResult then carries the *original* run's id, and
    -- saying so is the honest thing to report.
    cached INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE TABLE IF NOT EXISTS spend (
    day TEXT PRIMARY KEY,
    cost_usd REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS ip_quota (
    day TEXT NOT NULL,
    ip TEXT NOT NULL,
    submissions INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, ip)
);
CREATE TABLE IF NOT EXISTS answer_cache (
    cache_key TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
"""


def utcnow() -> datetime:
    """The service's clock. UTC everywhere: the day boundary the spend cap
    and the per-IP quota reset on has to be one documented instant, not
    whatever timezone the box happens to be set to."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class JobRow:
    """One submitted question and where its run got to."""

    id: str
    status: JobStatus
    question: str
    companies: list[str]
    client_ip: str
    created_at: str
    updated_at: str
    result_json: str | None
    error: str | None
    cost_usd: float
    tokens: int
    cached: bool


class ApiStore:
    """Jobs, spend, quota and cache over one SQLite file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sqlite3.threadsafety < 3:  # pragma: no cover - build-dependent
            raise RuntimeError("SQLite build is not serialized-mode; cannot share connections")
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Jobs ---

    def create_job(
        self, *, job_id: str, question: str, companies: list[str], client_ip: str
    ) -> JobRow:
        now = utcnow().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, status, question, companies, client_ip, "
                "created_at, updated_at) VALUES (?, 'queued', ?, ?, ?, ?, ?)",
                (job_id, question, ",".join(companies), client_ip, now, now),
            )
            self._conn.commit()
        row = self.get_job(job_id)
        assert row is not None
        return row

    def get_job(self, job_id: str) -> JobRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _to_job(row) if row else None

    def set_status(self, job_id: str, status: JobStatus, *, error: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, utcnow().isoformat(), job_id),
            )
            self._conn.commit()

    def save_result(
        self,
        job_id: str,
        *,
        status: JobStatus,
        result_json: str,
        cost_usd: float,
        tokens: int,
        error: str | None = None,
        cached: bool = False,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, result_json = ?, cost_usd = ?, tokens = ?, "
                "error = ?, cached = ?, updated_at = ? WHERE id = ?",
                (
                    status,
                    result_json,
                    cost_usd,
                    tokens,
                    error,
                    int(cached),
                    utcnow().isoformat(),
                    job_id,
                ),
            )
            self._conn.commit()

    def recover_orphans(self) -> list[str]:
        """Mark jobs the previous process was still working on as
        `interrupted` and return their ids.

        `queued` counts as orphaned alongside `running`: its asyncio task
        died with the process just as surely, and leaving it `queued` would
        advertise a job nothing will ever pick up. Both are resumable from
        their LangGraph checkpoint — a `queued` job simply has none yet and
        restarts from the top."""
        with self._lock:
            placeholders = ",".join("?" for _ in LIVE_STATUSES)
            rows = self._conn.execute(
                f"SELECT id FROM jobs WHERE status IN ({placeholders})", LIVE_STATUSES
            ).fetchall()
            ids = [str(r["id"]) for r in rows]
            if ids:
                self._conn.execute(
                    f"UPDATE jobs SET status = 'interrupted', updated_at = ? "
                    f"WHERE status IN ({placeholders})",
                    (utcnow().isoformat(), *LIVE_STATUSES),
                )
                self._conn.commit()
        return ids

    # --- Spend ledger ---

    def record_spend(self, day: str, cost_usd: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spend (day, cost_usd) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET cost_usd = cost_usd + excluded.cost_usd",
                (day, cost_usd),
            )
            self._conn.commit()

    def spend_on(self, day: str) -> float:
        with self._lock:
            row = self._conn.execute("SELECT cost_usd FROM spend WHERE day = ?", (day,)).fetchone()
        return float(row["cost_usd"]) if row else 0.0

    def spend_in_month(self, month: str) -> float:
        """`month` is a `YYYY-MM` prefix. Summing the daily rows means one
        table serves both caps and they can never disagree."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM spend WHERE day LIKE ?",
                (f"{month}-%",),
            ).fetchone()
        return float(row["total"])

    # --- Per-IP quota ---

    def ip_submissions(self, day: str, ip: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT submissions FROM ip_quota WHERE day = ? AND ip = ?", (day, ip)
            ).fetchone()
        return int(row["submissions"]) if row else 0

    def bump_ip(self, day: str, ip: str) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO ip_quota (day, ip, submissions) VALUES (?, ?, 1) "
                "ON CONFLICT(day, ip) DO UPDATE SET submissions = submissions + 1 "
                "RETURNING submissions",
                (day, ip),
            )
            count = int(cursor.fetchone()["submissions"])
            self._conn.commit()
        return count

    # --- Answer cache ---

    def cache_get(self, cache_key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM answer_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE answer_cache SET hits = hits + 1 WHERE cache_key = ?", (cache_key,)
            )
            self._conn.commit()
        return str(row["result_json"])

    def cache_put(self, cache_key: str, result_json: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO answer_cache (cache_key, result_json, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET result_json = excluded.result_json",
                (cache_key, result_json, utcnow().isoformat()),
            )
            self._conn.commit()


def _to_job(row: sqlite3.Row) -> JobRow:
    companies = str(row["companies"])
    return JobRow(
        id=str(row["id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        question=str(row["question"]),
        companies=[c for c in companies.split(",") if c],
        client_ip=str(row["client_ip"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        result_json=row["result_json"],
        error=row["error"],
        cost_usd=float(row["cost_usd"]),
        tokens=int(row["tokens"]),
        cached=bool(row["cached"]),
    )
