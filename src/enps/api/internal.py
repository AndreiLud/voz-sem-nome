"""Internal API (port 8002), reachable only by n8n on the Docker network.

Every route requires X-Internal-Token equal to INTERNAL_API_TOKEN.

    uvicorn enps.api.internal:app --port 8002 --no-access-log
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from enps import alerts, config
from enps.api.common import AppContext, error, get_conn, make_app, secret_matches
from enps.metrics import production
from enps.queue.ingest import DuplicateSubmission, InvalidSubmission, ingest, validate_form_row
from enps.storage import retention
from enps.storage.timeutil import Clock, utc_now


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def require_token(request: Request, x_internal_token: str | None = Header(None)) -> None:
    ctx = _ctx(request)
    if not secret_matches(x_internal_token, ctx.env.get("INTERNAL_API_TOKEN"), dev=ctx.dev):
        raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/v1/internal", dependencies=[Depends(require_token)])


@router.post("/responses", status_code=202)
def add_response(
    request: Request, payload: Any = Body(None), conn: sqlite3.Connection = Depends(get_conn)
) -> JSONResponse:
    """One Google Forms row forwarded by n8n (public dataset, channel google_forms)."""
    ctx = _ctx(request)
    now = ctx.clock()
    try:
        sub = validate_form_row(payload, ctx.cfg, now)
    except InvalidSubmission as exc:
        return error(422, "validation", fields=exc.fields)
    try:
        accepted = ingest(conn, sub, channel="google_forms", cfg=ctx.cfg, now=now)
    except DuplicateSubmission:
        return error(409, "duplicate")
    return JSONResponse(status_code=202,
                        content={"public_id": accepted.public_id, "status": accepted.status})


@router.get("/alerts/pending")
def pending_alerts(
    request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    ctx = _ctx(request)
    return alerts.pending_payload(conn, ctx.clock(), ctx.cfg)


@router.post("/alerts/sent")
def digest_sent(
    request: Request, payload: Any = Body(None), conn: sqlite3.Connection = Depends(get_conn)
) -> Any:
    ids = payload.get("ids") if isinstance(payload, dict) else None
    if (not isinstance(ids, list) or not ids
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        return error(422, "validation", fields={"ids": "must be a non-empty list of alert ids"})
    return {"updated": alerts.mark_sent(conn, ids, _ctx(request).clock())}


@router.post("/alerts/{alert_id}/sent")
def alert_sent(
    alert_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> Any:
    if conn.execute("SELECT 1 FROM alerts WHERE id = ?", (alert_id,)).fetchone() is None:
        return error(404, "not_found")
    alerts.mark_sent(conn, [alert_id], _ctx(request).clock())
    return {"id": alert_id, "status": "sent"}


@router.post("/alerts/{alert_id}/failed")
def alert_failed(alert_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> Any:
    status = alerts.mark_failed(conn, alert_id)
    if status is None:
        return error(404, "not_found")
    return {"id": alert_id, "status": status}


@router.post("/maintenance/purge")
def purge(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, int]:
    ctx = _ctx(request)
    return retention.purge_expired(conn, ctx.clock(), ctx.cfg)


@router.get("/summary/daily")
def daily_summary(
    request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> dict[str, Any]:
    summary = production.daily_summary(conn, _ctx(request).clock())
    return {**summary, "channels": alerts.channels(config.settings())}


@router.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, str]:
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok"}


def create_app(
    *,
    db_path: str | Path | None = None,
    cfg: Mapping[str, Any] | None = None,
    clock: Clock = utc_now,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    cfg = cfg or config.settings()
    ctx = AppContext(
        db_path=Path(db_path) if db_path else config.resolve(cfg["paths"]["database"]),
        cfg=cfg, clock=clock, env=dict(os.environ) if env is None else env,
    )
    app = make_app(ctx, "Voz sem nome internal API")
    app.include_router(router)
    return app


app = create_app()
