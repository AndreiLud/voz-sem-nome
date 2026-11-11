"""Queue worker: classifies one job at a time with the local model.

    python -m enps.queue.worker                  # run until SIGTERM (stops after the current job)
    python -m enps.queue.worker --drain          # process until the queue is empty, then exit
    python -m enps.queue.worker --db data/pipeline/synthetic.db --drain
"""
from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from enps import alerts, config
from enps.classify.types import ClassificationResult
from enps.storage import db
from enps.storage.timeutil import Clock, minute_floor, parse_iso, sp_day, to_iso, utc_now

log = logging.getLogger("enps.worker")

ClassifyFn = Callable[[str], ClassificationResult]
RESULT_STATUSES = ("done", "review", "skipped")


def default_classify(text: str) -> ClassificationResult:
    from enps.classify.pipeline import classify_comment  # loads the model client lazily

    return classify_comment(text)


def _ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


class Worker:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        classify: ClassifyFn | None = None,
        cfg: Mapping[str, Any] | None = None,
        clock: Clock = utc_now,
        notifier: alerts.Notifier | None = None,
        poll_s: float = 1.0,
        out: TextIO | None = None,
    ) -> None:
        self.conn = conn
        self.classify = classify or default_classify
        self.cfg = cfg or config.settings()
        self.clock = clock
        self.notifier = notifier
        self.poll_s = poll_s
        self.out = out
        self._stop = threading.Event()
        self._last_sample: str | None = None

    def stop(self) -> None:
        """Finish the current job, then leave the loop."""
        self._stop.set()

    def run(self, *, drain: bool = False) -> int:
        """Process jobs until stopped (or, with drain, until the queue is empty)."""
        stale = db.reset_stale_processing(self.conn)
        if stale:
            log.info("requeued %d stale jobs", stale)
        total = db.queue_counts(self.conn)["pending"]
        done = 0
        while not self._stop.is_set():
            self._tick()
            job = db.claim_next_job(self.conn, to_iso(self.clock()))
            if job is None:
                if drain:
                    break
                self._stop.wait(self.poll_s)
                continue
            status, seconds = self.process(job)
            done += 1
            if drain:
                self._progress(done, max(total, done), job, status, seconds)
            if status == "queued":
                self._stop.wait(self.poll_s)  # give a failing model a moment before retrying
        self._tick()
        return done

    def _tick(self) -> None:
        """Heartbeat on every loop; a queue sample at most once per minute."""
        now = self.clock()
        db.set_meta(self.conn, db.WORKER_HEARTBEAT_KEY, to_iso(now))
        minute = to_iso(minute_floor(now))
        if minute != self._last_sample:
            counts = db.queue_counts(self.conn)
            db.add_queue_sample(self.conn, minute, counts["pending"], counts["processing"])
            self._last_sample = minute

    def process(self, job: Mapping[str, Any]) -> tuple[str, float]:
        """Classify one claimed job; returns its new status and the seconds it took."""
        started = parse_iso(job["started_at"])
        try:
            result = self.classify(job["comment_anon"])
            if result.status not in RESULT_STATUSES:
                raise ValueError(f"unexpected status {result.status!r}")
        except Exception as exc:  # the worker must survive any model or parsing error
            log.warning("job %d attempt %d failed: %s", job["id"], job["attempts"],
                        type(exc).__name__)
            with db.transaction(self.conn):
                status = db.requeue_or_fail(self.conn, job, self.cfg["queue"]["max_attempts"],
                                            to_iso(self.clock()))
            return status, (self.clock() - started).total_seconds()
        finished = self.clock()
        new_alert = self._save(job, result, started, finished)
        if new_alert and new_alert.mode == "immediate":
            self._notify(new_alert.id)
        return result.status, (finished - started).total_seconds()

    def _notify(self, alert_id: int) -> None:
        """Send an immediate alert. On any error it stays pending for the polling route."""
        if self.notifier is None:
            return
        try:
            self.notifier(self.conn, alert_id)
        except Exception as exc:  # a delivery problem must never stop the queue
            log.warning("alert %d not delivered: %s", alert_id, type(exc).__name__)

    def _save(
        self, job: Mapping[str, Any], result: ClassificationResult,
        started: datetime, finished: datetime,
    ) -> alerts.NewAlert | None:
        queued = parse_iso(job["queued_at"])
        day = sp_day(queued)
        finished_iso = to_iso(finished)
        new_alert = None
        with db.transaction(self.conn):
            if result.status != "skipped":
                db.save_classification(self.conn, job["id"], result, finished_iso)
                db.bump_daily(self.conn, day, job["dataset"], job["channel"],
                              classified=1, critical=int(result.critical))
                db.add_latency_sample(
                    self.conn, day=day, dataset=job["dataset"], channel=job["channel"],
                    queue_ms=_ms(queued, started), processing_ms=_ms(started, finished))
                if result.critical:
                    new_alert = alerts.create_alert(
                        self.conn, job, result.critical_types, finished, self.cfg)
            db.finish_job(self.conn, job["id"], result.status, finished_iso)
        return new_alert

    def _progress(
        self, done: int, total: int, job: Mapping[str, Any], status: str, seconds: float
    ) -> None:
        if self.out is None:
            return
        ref = job["source_ref"] or job["public_id"]
        print(f"[{done}/{total}] {ref} {status} {seconds:.1f} s", file=self.out, flush=True)


def drain(
    db_path: str | Path | None = None,
    *,
    classify: ClassifyFn | None = None,
    cfg: Mapping[str, Any] | None = None,
    out: TextIO | None = sys.stdout,
) -> int:
    """Process every queued job of a database and return how many were handled."""
    conn = db.open_db(db_path)
    try:
        return Worker(conn, classify=classify, cfg=cfg, poll_s=0.0, out=out).run(drain=True)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m enps.queue.worker")
    parser.add_argument("--db", help="database file (default: settings paths.database)")
    parser.add_argument("--drain", action="store_true",
                        help="process until the queue is empty, then exit")
    parser.add_argument("--poll", type=float, default=1.0, help="idle wait in seconds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = db.open_db(args.db)
    worker = Worker(conn, notifier=alerts.env_notifier(), poll_s=args.poll, out=sys.stdout)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop())
    try:
        done = worker.run(drain=args.drain)
    finally:
        conn.close()
    log.info("worker stopped after %d jobs", done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
