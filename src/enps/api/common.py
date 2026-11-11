"""Pieces shared by the public and the internal API."""
from __future__ import annotations

import hmac
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from enps.storage import db
from enps.storage.timeutil import Clock


@dataclass
class AppContext:
    """Settings, clock and database location of one app instance."""
    db_path: Path
    cfg: Mapping[str, Any]
    clock: Clock
    env: Mapping[str, str]
    _ready: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def dev(self) -> bool:
        return self.env.get("ENPS_ENV") == "dev"

    def connect(self) -> sqlite3.Connection:
        """New connection; the schema is applied on first use, not at import time."""
        conn = db.connect(self.db_path)
        with self._lock:
            if not self._ready:
                db.init_db(conn)
                self._ready = True
        return conn


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = request.app.state.ctx.connect()
    try:
        yield conn
    finally:
        conn.close()


def secret_matches(given: str | None, expected: str | None, *, dev: bool) -> bool:
    """Constant-time comparison. Without a configured secret only dev mode is allowed."""
    if not expected:
        return dev
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


def error(status: int, code: str, headers: Mapping[str, str] | None = None,
          **extra: Any) -> JSONResponse:
    return JSONResponse({"error": code, **extra}, status_code=status, headers=dict(headers or {}))


def _validation_fields(exc: RequestValidationError) -> dict[str, str]:
    """Field errors without echoing the submitted values."""
    fields = {}
    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", ()) if part != "body"]
        fields[".".join(loc) or "body"] = item.get("msg", "invalid")
    return fields


def make_app(ctx: AppContext, title: str) -> FastAPI:
    """FastAPI app without docs outside dev, with compact JSON errors."""
    docs = ctx.dev
    app = FastAPI(
        title=title,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.ctx = ctx

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error(422, "validation", fields=_validation_fields(exc))

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        return error(exc.status_code, str(exc.detail), headers=exc.headers)

    return app
