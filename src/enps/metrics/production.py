"""Production numbers of the public demo: volume, latency, alerts and the daily summary."""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from enps.storage import db
from enps.storage.timeutil import seconds_between, sp_day, sp_day_start, to_iso


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def _seconds(ms: float | None) -> float | None:
    return None if ms is None else round(ms / 1000, 1)


def production_stats(conn: sqlite3.Connection, dataset: str = "public") -> dict[str, Any]:
    sums = conn.execute(
        """
        SELECT coalesce(sum(submissions), 0) AS submissions,
               coalesce(sum(distinct_submitters), 0) AS submitters,
               coalesce(sum(classified), 0) AS classified,
               min(CASE WHEN submissions > 0 THEN day END) AS first_day
          FROM daily_stats WHERE dataset = ?
        """,
        (dataset,),
    ).fetchone()
    latencies = [r["total_ms"] for r in conn.execute(
        "SELECT total_ms FROM latency_samples WHERE dataset = ?", (dataset,))]
    sent = conn.execute(
        "SELECT submitted_at, sent_at FROM alerts WHERE dataset = ? AND status = 'sent' "
        "AND sent_at IS NOT NULL",
        (dataset,),
    ).fetchall()
    delays = [seconds_between(r["submitted_at"], r["sent_at"]) for r in sent]
    return {
        "total_submissions": sums["submissions"],
        "distinct_submitters": sums["submitters"],
        "classified": sums["classified"],
        "latency_p50_seconds": _seconds(percentile(latencies, 50)),
        "latency_p95_seconds": _seconds(percentile(latencies, 95)),
        "alerts_sent": len(sent),
        "mean_submission_to_alert_seconds": round(sum(delays) / len(delays), 1) if delays else None,
        "first_public_day": sums["first_day"],
    }


def queue_series(conn: sqlite3.Connection, now: datetime, hours: int = 24) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ts, pending, processing FROM queue_samples WHERE ts >= ? ORDER BY ts",
        (to_iso(now - timedelta(hours=hours)),),
    ).fetchall()
    return [dict(row) for row in rows]


def daily_summary(
    conn: sqlite3.Connection, now: datetime, dataset: str = "public"
) -> dict[str, Any]:
    """Today's counters (Sao Paulo day) and a short Telegram HTML message in Portuguese."""
    day = sp_day(now)
    row = conn.execute(
        """
        SELECT coalesce(sum(submissions), 0) AS submissions,
               coalesce(sum(distinct_submitters), 0) AS distinct_submitters,
               coalesce(sum(classified), 0) AS classified,
               coalesce(sum(critical), 0) AS critical,
               coalesce(sum(rejected_rate_limit), 0) AS rejected_rate_limit,
               coalesce(sum(rejected_duplicate), 0) AS rejected_duplicate,
               coalesce(sum(rejected_queue_full), 0) AS rejected_queue_full
          FROM daily_stats WHERE day = ? AND dataset = ?
        """,
        (day, dataset),
    ).fetchone()
    counts = dict(row)
    counts["alerts_sent"] = conn.execute(
        "SELECT count(*) AS n FROM alerts WHERE dataset = ? AND status = 'sent' AND sent_at >= ?",
        (dataset, to_iso(sp_day_start(day))),
    ).fetchone()["n"]
    counts["queue_pending"] = db.queue_counts(conn)["pending"]
    return {"day": day, **counts, "message_html": _summary_html(day, counts)}


def _summary_html(day: str, c: dict[str, int]) -> str:
    rejected = c["rejected_rate_limit"] + c["rejected_duplicate"] + c["rejected_queue_full"]
    date = f"{day[8:10]}/{day[5:7]}/{day[0:4]}"
    return "\n".join([
        f"<b>Resumo do dia {date}</b>",
        f"Respostas: {c['submissions']} ({c['distinct_submitters']} pessoas diferentes pelo site)",
        f"Classificadas: {c['classified']}",
        f"Casos críticos: {c['critical']}, alertas enviados: {c['alerts_sent']}",
        f"Recusadas: {rejected} (limite {c['rejected_rate_limit']}, duplicadas "
        f"{c['rejected_duplicate']}, fila cheia {c['rejected_queue_full']})",
        f"Na fila agora: {c['queue_pending']}",
    ])
