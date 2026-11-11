"""Public API (port 8001), the only service reachable from the internet.

Requests arrive through the Cloudflare Worker, which adds X-Origin-Secret and X-Client-IP
and serves the site from the same origin (so there is no CORS here). Request bodies and
IP addresses are never logged or stored.

    uvicorn enps.api.public:app --port 8001 --no-access-log
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from enps import config
from enps.api.common import AppContext, error, get_conn, make_app, secret_matches
from enps.api.ratelimit import Limits, RateLimited, RateLimiter, SubmitterCounter
from enps.metrics import production
from enps.metrics.aggregates import build_dashboard
from enps.queue import eta
from enps.queue.ingest import (
    DuplicateSubmission, InvalidSubmission, format_tracking_code, ingest, tracking_hash,
    validate_site,
)
from enps.storage import db
from enps.storage.timeutil import Clock, seconds_between, sp_day, to_iso, utc_now

MAX_BODY_BYTES = 16_384
DASHBOARD_TTL_S = 30.0
DATASETS = ("public", "synthetic")
QUEUE_FULL_RETRY_S = 60
WAITING = ("queued", "processing")


@dataclass
class PublicState:
    limiter: RateLimiter
    submitters: SubmitterCounter
    pipeline_db_path: Path
    dashboard_cache: dict[str, tuple[datetime, dict[str, Any]]] = field(default_factory=dict)


router = APIRouter(prefix="/v1/public")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _state(request: Request) -> PublicState:
    return request.app.state.public


def _queue_info(
    conn: sqlite3.Connection, cfg: Mapping[str, Any], job: Mapping[str, Any]
) -> dict[str, int | None]:
    position = eta.job_position(conn, job)
    if position is None:
        return {"queue_position": None, "eta_seconds": None}
    average = eta.average_job_seconds(conn, cfg["queue"]["eta_window"])
    return {"queue_position": position, "eta_seconds": eta.eta_seconds(position, average)}


def _result(conn: sqlite3.Connection, row: Mapping[str, Any]) -> dict[str, Any]:
    c = db.classification_for(conn, row["id"])
    if c is None:
        return {}
    processing = (seconds_between(row["started_at"], row["finished_at"])
                  if row["started_at"] and row["finished_at"] else None)
    return {
        "themes": json.loads(c["themes"]),
        "sentiment": c["sentiment"],
        "critical": bool(c["critical"]),
        "critical_types": json.loads(c["critical_types"]),
        "evidence": c["evidence"],
        "model": c["model"],
        "processing_seconds": round(processing, 1) if processing is not None else None,
    }


def _rate_limited(conn: sqlite3.Connection, day: str, exc: RateLimited) -> JSONResponse:
    db.bump_daily(conn, day, "public", "site", rejected_rate_limit=1)
    return error(429, "rate_limited", headers={"Retry-After": str(exc.retry_after)},
                 scope=exc.scope, retry_after=exc.retry_after)


@router.post("/submissions", status_code=202)
def submit(
    request: Request,
    payload: Any = Body(None),
    x_origin_secret: str | None = Header(None),
    x_client_ip: str | None = Header(None),
    conn: sqlite3.Connection = Depends(get_conn),
) -> JSONResponse:
    ctx, state = _ctx(request), _state(request)
    if not secret_matches(x_origin_secret, ctx.env.get("ORIGIN_SHARED_SECRET"), dev=ctx.dev):
        return error(403, "forbidden")
    ip = x_client_ip or (request.client.host if request.client else "unknown")
    now = ctx.clock()
    day = sp_day(now)
    try:
        state.limiter.check(ip)
    except RateLimited as exc:
        return _rate_limited(conn, day, exc)
    try:
        sub = validate_site(payload, ctx.cfg)
    except InvalidSubmission as exc:
        return error(422, "validation", fields=exc.fields)
    if db.queue_counts(conn)["pending"] >= ctx.cfg["queue"]["max_pending"]:
        db.bump_daily(conn, day, "public", "site", rejected_queue_full=1)
        return error(503, "queue_full", headers={"Retry-After": str(QUEUE_FULL_RETRY_S)})
    try:
        state.limiter.record(ip)
    except RateLimited as exc:  # a concurrent request from the same IP took the last slot
        return _rate_limited(conn, day, exc)
    try:
        accepted = ingest(conn, sub, channel="site", cfg=ctx.cfg, now=now)
    except DuplicateSubmission:
        return error(409, "duplicate")
    if state.submitters.is_new(ip, day):
        db.bump_daily(conn, day, "public", "site", distinct_submitters=1)
    job = {"id": accepted.response_id, "status": accepted.status, "priority": accepted.priority}
    return JSONResponse(status_code=202, content={
        "code": accepted.tracking_code,
        "status": accepted.status,
        **_queue_info(conn, ctx.cfg, job),
        "comment_anon": accepted.comment_anon,
        "pii_counts": accepted.pii_counts,
    })


@router.get("/submissions/{code}", response_model=None)
def submission(
    code: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any] | JSONResponse:
    hashed = tracking_hash(code)
    row = db.response_by_tracking(conn, hashed) if hashed else None
    if row is None:
        return error(404, "not_found")
    body: dict[str, Any] = {
        "code": format_tracking_code(code),
        "status": row["status"],
        "comment_anon": row["comment_anon"],
        "pii_counts": json.loads(row["pii_counts"]),
    }
    if row["status"] in WAITING:
        body.update(_queue_info(conn, _ctx(request).cfg, row))
    elif row["status"] in db.FINISHED_STATUSES:
        body.update(_result(conn, row))
    return body


def _dataset_connection(ctx: AppContext, state: PublicState, dataset: str) -> sqlite3.Connection:
    """The synthetic dataset lives in the pipeline database when that file exists."""
    if dataset == "synthetic" and state.pipeline_db_path.exists():
        return db.connect(state.pipeline_db_path)
    return ctx.connect()


@router.get("/dashboard", response_model=None)
def dashboard(request: Request, dataset: str = "public") -> dict[str, Any] | JSONResponse:
    if dataset not in DATASETS:
        return error(422, "validation", fields={"dataset": "must be public or synthetic"})
    ctx, state = _ctx(request), _state(request)
    now = ctx.clock()
    cached = state.dashboard_cache.get(dataset)
    if cached and (now - cached[0]).total_seconds() < DASHBOARD_TTL_S:
        return cached[1]
    conn = _dataset_connection(ctx, state, dataset)
    try:
        data = build_dashboard(conn, dataset, cfg=ctx.cfg, now=now)
    finally:
        conn.close()
    state.dashboard_cache[dataset] = (now, data)
    return data


@router.get("/status")
def status(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
    ctx = _ctx(request)
    queue_cfg = ctx.cfg["queue"]
    now = ctx.clock()
    average = eta.average_job_seconds(conn, queue_cfg["eta_window"])
    new_position = eta.new_job_position(conn, queue_cfg["live_priority"])
    heartbeat = db.get_meta(conn, db.WORKER_HEARTBEAT_KEY)
    return {
        "generated_at": to_iso(now),
        "queue": {
            **db.queue_counts(conn),
            "max_pending": queue_cfg["max_pending"],
            "average_job_seconds": round(average, 1),
            "eta_seconds_new_job": eta.eta_seconds(new_position, average),
        },
        "worker": {
            "heartbeat_age_seconds":
                round(seconds_between(heartbeat, to_iso(now)), 1) if heartbeat else None,
        },
        "production": production.production_stats(conn, "public"),
        "queue_series": production.queue_series(conn, now),
    }


@router.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, str]:
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok"}


async def _limit_body(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return error(413, "too_large")
    return await call_next(request)


def create_app(
    *,
    db_path: str | Path | None = None,
    pipeline_db_path: str | Path | None = None,
    cfg: Mapping[str, Any] | None = None,
    clock: Clock = utc_now,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    cfg = cfg or config.settings()
    paths = cfg["paths"]
    ctx = AppContext(
        db_path=Path(db_path) if db_path else config.resolve(paths["database"]),
        cfg=cfg, clock=clock, env=dict(os.environ) if env is None else env,
    )
    app = make_app(ctx, "Voz sem nome public API")
    app.state.public = PublicState(
        limiter=RateLimiter(Limits.from_settings(cfg), clock),
        submitters=SubmitterCounter(),
        pipeline_db_path=(Path(pipeline_db_path) if pipeline_db_path
                          else config.resolve(paths["pipeline_database"])),
    )
    app.middleware("http")(_limit_body)
    app.include_router(router)
    return app


app = create_app()
