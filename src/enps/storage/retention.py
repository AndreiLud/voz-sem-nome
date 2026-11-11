"""Retention: public submissions, alert rows and queue samples are deleted after a while."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from enps import config
from enps.storage import db
from enps.storage.timeutil import to_iso

QUEUE_SAMPLE_RETENTION = timedelta(days=7)


def purge_expired(
    conn: sqlite3.Connection, now: datetime, cfg: Mapping[str, Any] | None = None
) -> dict[str, int]:
    """Delete expired rows in one transaction and return how many went per table."""
    privacy = (cfg or config.settings())["privacy"]
    with db.transaction(conn):
        return {
            "responses": db.purge_public_responses(
                conn, to_iso(now - timedelta(days=privacy["public_retention_days"]))),
            "alerts": db.purge_alerts(
                conn, to_iso(now - timedelta(days=privacy["alert_log_retention_days"]))),
            "queue_samples": db.purge_queue_samples(conn, to_iso(now - QUEUE_SAMPLE_RETENTION)),
        }
