"""Queue position and waiting time estimates."""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from enps.storage import db

DEFAULT_JOB_SECONDS = 15.0  # used until the worker has finished a job


def average_job_seconds(conn: sqlite3.Connection, window: int) -> float:
    """Moving average of the processing time of the last `window` jobs."""
    samples = db.recent_processing_ms(conn, window)
    if not samples:
        return DEFAULT_JOB_SECONDS
    return sum(samples) / len(samples) / 1000.0


def eta_seconds(position: int, average_s: float) -> int:
    return int(math.ceil(position * average_s))


def job_position(conn: sqlite3.Connection, job: Mapping[str, Any]) -> int | None:
    """Jobs that must finish before this one has a result, counting itself.

    None when the job is no longer waiting.
    """
    if job["status"] == "processing":
        return 1
    if job["status"] != "queued":
        return None
    running = db.queue_counts(conn)["processing"]
    return db.queued_ahead(conn, job["priority"], job["id"]) + running + 1


def new_job_position(conn: sqlite3.Connection, priority: int) -> int:
    """Position a job submitted now with this priority would get."""
    running = db.queue_counts(conn)["processing"]
    return db.queued_at_or_above(conn, priority) + running + 1
