"""SQLite access shared by the APIs, the queue worker and the batch pipeline.

Connections run in autocommit mode; multi-statement changes use `transaction()`
(BEGIN IMMEDIATE), so a writer never waits halfway through a unit of work.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from enps import config
from enps.classify.types import ClassificationResult

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

RESPONSE_COLUMNS = frozenset({
    "public_id", "tracking_hash", "dataset", "channel", "source_ref", "submitted_at", "score",
    "area", "tenure", "lang", "comment_anon", "comment_chars", "pii_counts", "anonymizer_version",
    "dedupe_key", "status", "priority", "attempts", "queued_at", "started_at", "finished_at",
})
STAT_COLUMNS = frozenset({
    "submissions", "distinct_submitters", "rejected_rate_limit", "rejected_duplicate",
    "rejected_queue_full", "classified", "critical",
})
FINISHED_STATUSES = ("done", "review")
WORKER_HEARTBEAT_KEY = "worker_heartbeat"


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys, a 5 s busy timeout and Row results."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes (idempotent)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def default_path() -> Path:
    return config.resolve(config.settings()["paths"]["database"])


def open_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Connect to `path` (default: settings paths.database) and make sure the schema exists."""
    conn = connect(path if path is not None else default_path())
    init_db(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# ---------------------------------------------------------------- meta


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- responses


def insert_response(conn: sqlite3.Connection, row: Mapping[str, Any]) -> int:
    unknown = set(row) - RESPONSE_COLUMNS
    if unknown:
        raise ValueError(f"unknown response columns: {sorted(unknown)}")
    cols = list(row)
    sql = f"INSERT INTO responses ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    return int(conn.execute(sql, [row[c] for c in cols]).lastrowid)


def response_by_tracking(conn: sqlite3.Connection, tracking_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM responses WHERE tracking_hash = ?", (tracking_hash,)
    ).fetchone()


def response_by_public_id(conn: sqlite3.Connection, public_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM responses WHERE public_id = ?", (public_id,)).fetchone()


def has_recent_duplicate(
    conn: sqlite3.Connection, dataset: str, dedupe_key: str, since: str
) -> bool:
    """Same dedupe key received (queued) in the dataset since `since`.

    Uses the reception time, not submitted_at, so a resent old form row is still caught.
    """
    row = conn.execute(
        "SELECT 1 FROM responses WHERE dataset = ? AND dedupe_key = ? AND queued_at >= ? LIMIT 1",
        (dataset, dedupe_key, since),
    ).fetchone()
    return row is not None


def source_ref_exists(conn: sqlite3.Connection, dataset: str, source_ref: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM responses WHERE dataset = ? AND source_ref = ? LIMIT 1",
        (dataset, source_ref),
    ).fetchone()
    return row is not None


def classification_for(conn: sqlite3.Connection, response_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM classifications WHERE response_id = ?", (response_id,)
    ).fetchone()


# ---------------------------------------------------------------- queue


def claim_next_job(conn: sqlite3.Connection, now: str) -> sqlite3.Row | None:
    """Atomically move the next queued job (lowest priority, then id) to processing."""
    rows = conn.execute(
        """
        UPDATE responses
           SET status = 'processing', started_at = ?, attempts = attempts + 1
         WHERE id = (SELECT id FROM responses WHERE status = 'queued'
                      ORDER BY priority, id LIMIT 1)
        RETURNING *
        """,
        (now,),
    ).fetchall()
    return rows[0] if rows else None


def reset_stale_processing(conn: sqlite3.Connection) -> int:
    """Put jobs left in 'processing' by a crashed worker back in the queue."""
    cur = conn.execute(
        "UPDATE responses SET status = 'queued', started_at = NULL WHERE status = 'processing'"
    )
    return cur.rowcount


def finish_job(conn: sqlite3.Connection, response_id: int, status: str, now: str) -> None:
    conn.execute(
        "UPDATE responses SET status = ?, finished_at = ? WHERE id = ?",
        (status, now, response_id),
    )


def requeue_or_fail(
    conn: sqlite3.Connection, job: Mapping[str, Any], max_attempts: int, now: str
) -> str:
    """After an error: back to the queue, or 'failed' once max_attempts were used."""
    if job["attempts"] >= max_attempts:
        finish_job(conn, job["id"], "failed", now)
        return "failed"
    conn.execute(
        "UPDATE responses SET status = 'queued', started_at = NULL WHERE id = ?", (job["id"],)
    )
    return "queued"


def save_classification(
    conn: sqlite3.Connection, response_id: int, result: ClassificationResult, now: str
) -> None:
    stages = [dataclasses.asdict(stage) for stage in result.stages]
    conn.execute(
        """
        INSERT OR REPLACE INTO classifications (
            response_id, themes, sentiment, critical, critical_types, critical_sources, evidence,
            model, model_digest, quantization, runtime_version, prompt_version, verifier_version,
            stages, total_ms, output_tokens, prompt_tokens, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            response_id, json.dumps(result.themes), result.sentiment, int(result.critical),
            json.dumps(result.critical_types), json.dumps(result.critical_sources),
            result.evidence, result.model, result.model_digest, result.quantization,
            result.runtime_version, result.prompt_version, result.verifier_version,
            json.dumps(stages, ensure_ascii=False), result.total_ms, result.output_tokens,
            result.prompt_tokens, now,
        ),
    )


def queue_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, count(*) AS n FROM responses "
        "WHERE status IN ('queued', 'processing') GROUP BY status"
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {"pending": counts.get("queued", 0), "processing": counts.get("processing", 0)}


def queued_ahead(conn: sqlite3.Connection, priority: int, response_id: int) -> int:
    """Queued jobs that run before a job with this priority and id."""
    row = conn.execute(
        "SELECT count(*) AS n FROM responses WHERE status = 'queued' "
        "AND (priority < ? OR (priority = ? AND id < ?))",
        (priority, priority, response_id),
    ).fetchone()
    return int(row["n"])


def queued_at_or_above(conn: sqlite3.Connection, priority: int) -> int:
    """Queued jobs that a new job with this priority would wait behind."""
    row = conn.execute(
        "SELECT count(*) AS n FROM responses WHERE status = 'queued' AND priority <= ?",
        (priority,),
    ).fetchone()
    return int(row["n"])


def recent_processing_ms(conn: sqlite3.Connection, limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT processing_ms FROM latency_samples ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [int(r["processing_ms"]) for r in rows]


# ---------------------------------------------------------------- counters


def bump_daily(
    conn: sqlite3.Connection, day: str, dataset: str, channel: str, **increments: int
) -> None:
    """Add to daily_stats counters, creating the row when needed."""
    unknown = set(increments) - STAT_COLUMNS
    if unknown or not increments:
        raise ValueError(f"bad daily_stats counters: {sorted(unknown) or 'none'}")
    cols = list(increments)
    updates = ", ".join(f"{c} = {c} + excluded.{c}" for c in cols)
    conn.execute(
        f"INSERT INTO daily_stats (day, dataset, channel, {', '.join(cols)}) "
        f"VALUES (?, ?, ?, {', '.join('?' * len(cols))}) "
        f"ON CONFLICT (day, dataset, channel) DO UPDATE SET {updates}",
        [day, dataset, channel, *[increments[c] for c in cols]],
    )


def add_latency_sample(
    conn: sqlite3.Connection, *, day: str, dataset: str, channel: str,
    queue_ms: int, processing_ms: int,
) -> None:
    conn.execute(
        "INSERT INTO latency_samples (day, dataset, channel, queue_ms, processing_ms, total_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (day, dataset, channel, queue_ms, processing_ms, queue_ms + processing_ms),
    )


def add_queue_sample(conn: sqlite3.Connection, ts: str, pending: int, processing: int) -> bool:
    """Store one queue sample per timestamp; returns False when `ts` already exists."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO queue_samples (ts, pending, processing) VALUES (?, ?, ?)",
        (ts, pending, processing),
    )
    return cur.rowcount == 1


# ---------------------------------------------------------------- retention


def purge_public_responses(conn: sqlite3.Connection, before: str) -> int:
    """Delete public submissions older than `before`; classifications go with them."""
    cur = conn.execute(
        "DELETE FROM responses WHERE dataset = 'public' AND submitted_at < ?", (before,)
    )
    return cur.rowcount


def purge_alerts(conn: sqlite3.Connection, before: str) -> int:
    return conn.execute("DELETE FROM alerts WHERE detected_at < ?", (before,)).rowcount


def purge_queue_samples(conn: sqlite3.Connection, before: str) -> int:
    return conn.execute("DELETE FROM queue_samples WHERE ts < ?", (before,)).rowcount
