import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from enps.classify.types import ClassificationResult, StageRecord
from enps.storage import db, retention
from enps.storage.timeutil import parse_iso, sp_day, to_iso

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    connection = db.open_db(tmp_path / "app.db")
    yield connection
    connection.close()


def add(conn, *, dataset="public", priority=0, status="queued", submitted=NOW):
    row = {
        "public_id": secrets.token_hex(5),
        "dataset": dataset, "channel": "synthetic" if dataset == "synthetic" else "site",
        "submitted_at": to_iso(submitted), "score": 8, "area": "operacoes", "tenure": "lt1",
        "comment_anon": "texto qualquer", "anonymizer_version": "t", "status": status,
        "priority": priority, "queued_at": to_iso(submitted),
    }
    return db.insert_response(conn, row)


def test_connection_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    db.init_db(conn)  # idempotent


def test_claim_follows_priority_then_id(conn):
    batch = add(conn, priority=10)
    live_1 = add(conn, priority=0)
    live_2 = add(conn, priority=0)
    order = []
    while (job := db.claim_next_job(conn, to_iso(NOW))) is not None:
        assert job["status"] == "processing" and job["attempts"] == 1
        order.append(job["id"])
    assert order == [live_1, live_2, batch]
    assert db.queue_counts(conn) == {"pending": 0, "processing": 3}
    assert db.reset_stale_processing(conn) == 3
    assert db.queue_counts(conn) == {"pending": 3, "processing": 0}


def test_requeue_until_max_attempts(conn):
    add(conn)
    job = db.claim_next_job(conn, to_iso(NOW))
    assert db.requeue_or_fail(conn, job, 2, to_iso(NOW)) == "queued"
    job = db.claim_next_job(conn, to_iso(NOW))
    assert job["attempts"] == 2
    assert db.requeue_or_fail(conn, job, 2, to_iso(NOW)) == "failed"
    assert db.claim_next_job(conn, to_iso(NOW)) is None


def test_save_classification_and_counters(conn):
    rid = add(conn)
    result = ClassificationResult(
        status="done", themes=["lid", "car"], sentiment="neg", critical=True,
        critical_types=["bo"], critical_sources=["llm"], evidence="x", model="m",
        stages=[StageRecord(name="classify", total_ms=900, output_tokens=20, prompt_tokens=300)],
    )
    db.save_classification(conn, rid, result, to_iso(NOW))
    row = db.classification_for(conn, rid)
    assert json.loads(row["themes"]) == ["lid", "car"]
    assert json.loads(row["stages"])[0]["name"] == "classify"
    assert (row["total_ms"], row["output_tokens"], row["prompt_tokens"]) == (900, 20, 300)
    db.bump_daily(conn, "2026-11-11", "public", "site", submissions=1)
    db.bump_daily(conn, "2026-11-11", "public", "site", submissions=2, classified=1)
    stats = conn.execute("SELECT * FROM daily_stats").fetchone()
    assert (stats["submissions"], stats["classified"]) == (3, 1)
    with pytest.raises(ValueError):
        db.bump_daily(conn, "2026-11-11", "public", "site", ip=1)


def test_queue_sample_once_per_timestamp(conn):
    assert db.add_queue_sample(conn, to_iso(NOW), 1, 0)
    assert not db.add_queue_sample(conn, to_iso(NOW), 2, 0)


def test_purge_cascades_and_respects_retention(conn):
    old = NOW - timedelta(days=31)
    old_public = add(conn, submitted=old)
    add(conn, submitted=NOW - timedelta(days=29))
    add(conn, dataset="synthetic", submitted=old)
    db.save_classification(conn, old_public, ClassificationResult(status="done"), to_iso(old))
    conn.execute(
        "INSERT INTO alerts (response_public_id, dataset, critical_types, submitted_at, "
        "detected_at, mode, status) VALUES ('x', 'public', '[]', ?, ?, 'immediate', 'sent')",
        (to_iso(NOW - timedelta(days=91)), to_iso(NOW - timedelta(days=91))),
    )
    db.add_queue_sample(conn, to_iso(NOW - timedelta(days=8)), 0, 0)
    db.add_queue_sample(conn, to_iso(NOW - timedelta(days=1)), 0, 0)
    counts = retention.purge_expired(conn, NOW)
    assert counts == {"responses": 1, "alerts": 1, "queue_samples": 1}
    assert conn.execute("SELECT count(*) FROM classifications").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM responses").fetchone()[0] == 2


def test_time_helpers():
    assert to_iso(NOW) == "2026-11-11T15:00:00.000+00:00"
    assert sp_day(datetime(2026, 11, 11, 2, 30, tzinfo=timezone.utc)) == "2026-11-10"
    assert parse_iso("2026-03-01T09:00:00-03:00") == datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        to_iso(datetime(2026, 1, 1))
