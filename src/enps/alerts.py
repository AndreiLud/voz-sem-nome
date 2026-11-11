"""Critical-case alerts: creation, Telegram HTML messages, digests and delivery to n8n.

Alert rows keep only ids, types and timestamps. Messages are built when needed from the
anonymized comment, with profanity masked, and never carry personal data.
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

import httpx

from enps import config
from enps.metrics.aggregates import visible_area_codes
from enps.metrics.excerpt import public_excerpt
from enps.storage.timeutil import SAO_PAULO, parse_iso, to_iso

ALERT_TITLE = "Caso crítico na pesquisa de clima"
DIGEST_TITLE = "Casos críticos na pesquisa de clima"
AREA_HIDDEN = "Área omitida (grupo pequeno)"
LINK_TEXT = "Ver os casos críticos no painel"
IMMEDIATE_GRACE = timedelta(minutes=2)   # immediate alerts still pending after this are polled
MAX_DELIVERY_ATTEMPTS = 3
DIGEST_EXCERPTS = 5
WEBHOOK_TIMEOUT_S = 5.0

Notifier = Callable[[sqlite3.Connection, int], bool]


@dataclass(frozen=True)
class NewAlert:
    id: int
    mode: str  # "immediate" or "digest"


@dataclass(frozen=True)
class AlertView:
    id: int
    dataset: str
    mode: str
    types: list[str]
    themes: list[str]
    area: str | None       # None when the area is hidden by the minimum group rule
    excerpt: str
    submitted_at: str
    detected_at: str


def _esc(text: str) -> str:
    """Telegram HTML escaping: only <, > and & are special in text."""
    return html.escape(text, quote=False)


def _labels(items: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {item["code"]: item["label"]["pt"] for item in items}


def _dashboard_url(cfg: Mapping[str, Any]) -> str:
    site = cfg["site"]
    return site["base_url"].rstrip("/") + site["dashboard_path"]["pt"] + "#criticos"


def _link(cfg: Mapping[str, Any]) -> str:
    return f'<a href="{html.escape(_dashboard_url(cfg), quote=True)}">{LINK_TEXT}</a>'


# ---------------------------------------------------------------- creation


def choose_mode(
    conn: sqlite3.Connection, dataset: str, now: datetime, cfg: Mapping[str, Any]
) -> str:
    """Immediate while the dataset had fewer than alerts.immediate_per_hour in the last hour."""
    row = conn.execute(
        "SELECT count(*) AS n FROM alerts WHERE dataset = ? AND mode = 'immediate' "
        "AND detected_at >= ?",
        (dataset, to_iso(now - timedelta(hours=1))),
    ).fetchone()
    return "immediate" if row["n"] < cfg["alerts"]["immediate_per_hour"] else "digest"


def create_alert(
    conn: sqlite3.Connection,
    response: Mapping[str, Any],
    critical_types: Sequence[str],
    now: datetime,
    cfg: Mapping[str, Any] | None = None,
) -> NewAlert | None:
    """Store a pending alert for a critical response; None when its dataset sends no alerts."""
    cfg = cfg or config.settings()
    if response["dataset"] not in cfg["alerts"]["datasets"]:
        return None
    mode = choose_mode(conn, response["dataset"], now, cfg)
    cur = conn.execute(
        "INSERT INTO alerts (response_public_id, dataset, critical_types, submitted_at, "
        "detected_at, mode, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (response["public_id"], response["dataset"], json.dumps(list(critical_types)),
         response["submitted_at"], to_iso(now), mode),
    )
    return NewAlert(int(cur.lastrowid), mode)


# ---------------------------------------------------------------- views and messages


def load_views(
    conn: sqlite3.Connection, rows: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any]
) -> list[AlertView]:
    visible: dict[str, set[str]] = {}
    views = []
    for row in rows:
        dataset = row["dataset"]
        if dataset not in visible:
            visible[dataset] = visible_area_codes(conn, dataset, cfg)
        response = conn.execute(
            "SELECT r.area, r.comment_anon, c.themes FROM responses r "
            "LEFT JOIN classifications c ON c.response_id = r.id WHERE r.public_id = ?",
            (row["response_public_id"],),
        ).fetchone()
        area = response["area"] if response else None
        views.append(AlertView(
            id=row["id"], dataset=dataset, mode=row["mode"],
            types=json.loads(row["critical_types"]),
            themes=json.loads(response["themes"]) if response and response["themes"] else [],
            area=area if area in visible[dataset] else None,
            excerpt=public_excerpt(response["comment_anon"],
                                   cfg["privacy"]["display_excerpt_chars"]) if response else "",
            submitted_at=row["submitted_at"], detected_at=row["detected_at"],
        ))
    return views


def message_html(view: AlertView, cfg: Mapping[str, Any]) -> str:
    """Telegram HTML message for one critical case, in Portuguese."""
    type_labels = _labels(config.taxonomy()["critical_types"])
    theme_labels = _labels(config.taxonomy()["themes"])
    area_labels = _labels(cfg["survey"]["areas"])
    types = ", ".join(type_labels.get(t, t) for t in view.types)
    received = parse_iso(view.submitted_at).astimezone(SAO_PAULO)
    lines = [f"<b>{_esc(ALERT_TITLE)}</b>",
             f"{'Tipos' if len(view.types) > 1 else 'Tipo'}: {_esc(types)}"]
    if view.themes:
        themes = ", ".join(theme_labels.get(t, t) for t in view.themes)
        lines.append(f"Temas: {_esc(themes)}")
    lines.append(f"Área: {_esc(area_labels.get(view.area, view.area))}" if view.area
                 else AREA_HIDDEN)
    lines.append(f"Recebido em {received:%d/%m/%Y}")  # no time of day: it narrows who wrote it
    if view.excerpt:
        lines.append(f"<i>“{_esc(view.excerpt)}”</i>")
    lines.append(_link(cfg))
    return "\n".join(lines)


def channels(cfg: Mapping[str, Any]) -> list[str]:
    """External channels n8n should use besides the local record (alerts.channels, empty by default)."""
    return [str(c) for c in cfg["alerts"].get("channels") or []]


def view_payload(view: AlertView, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """JSON sent to n8n (webhook and polling) for one alert."""
    return {
        "alert_id": view.id, "dataset": view.dataset, "mode": view.mode, "types": view.types,
        "themes": view.themes, "area": view.area, "excerpt": view.excerpt,
        "submitted_at": view.submitted_at, "detected_at": view.detected_at,
        "message_html": message_html(view, cfg),
        "channels": channels(cfg),
    }


def build_digest(views: Sequence[AlertView], cfg: Mapping[str, Any]) -> dict[str, Any]:
    """One message for several alerts: counts by type and up to five excerpts."""
    type_labels = _labels(config.taxonomy()["critical_types"])
    area_labels = _labels(cfg["survey"]["areas"])
    by_type = Counter(t for view in views for t in view.types)
    count = len(views)
    lines = [f"<b>{_esc(DIGEST_TITLE)}</b>",
             "1 caso novo." if count == 1 else f"{count} casos novos.", ""]
    lines += [f"{_esc(label)}: {by_type[code]}" for code, label in type_labels.items()
              if by_type[code]]
    lines.append("")
    for number, view in enumerate(views[:DIGEST_EXCERPTS], start=1):
        area = f" ({_esc(area_labels.get(view.area, view.area))})" if view.area else ""
        lines.append(f"{number}. <i>“{_esc(view.excerpt)}”</i>{area}")
    if count > DIGEST_EXCERPTS:
        lines.append(f"Mais {count - DIGEST_EXCERPTS} no painel.")
    lines.append(_link(cfg))
    return {
        "ids": [view.id for view in views],
        "count": count,
        "by_type": {code: by_type[code] for code in type_labels if by_type[code]},
        "message_html": "\n".join(lines),
    }


# ---------------------------------------------------------------- delivery


def pending_payload(
    conn: sqlite3.Connection, now: datetime, cfg: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """What n8n should send now: stale immediate alerts and, when due, the digest.

    The digest is due once its oldest alert has waited alerts.digest_every_minutes.
    """
    cfg = cfg or config.settings()
    immediate = conn.execute(
        "SELECT * FROM alerts WHERE status = 'pending' AND mode = 'immediate' "
        "AND detected_at <= ? ORDER BY id",
        (to_iso(now - IMMEDIATE_GRACE),),
    ).fetchall()
    digest = conn.execute(
        "SELECT * FROM alerts WHERE status = 'pending' AND mode = 'digest' ORDER BY id"
    ).fetchall()
    due_before = to_iso(now - timedelta(minutes=cfg["alerts"]["digest_every_minutes"]))
    digest_due = bool(digest) and digest[0]["detected_at"] <= due_before
    return {
        "immediate": [view_payload(v, cfg) for v in load_views(conn, immediate, cfg)],
        "digest": build_digest(load_views(conn, digest, cfg), cfg) if digest_due else None,
        "digest_waiting": len(digest),
        "channels": channels(cfg),
    }


def mark_sent(conn: sqlite3.Connection, ids: Sequence[int], now: datetime) -> int:
    """Mark alerts as delivered; returns how many changed (already sent ones are kept)."""
    if not ids:
        return 0
    marks = ", ".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE alerts SET status = 'sent', sent_at = ? WHERE id IN ({marks}) "
        "AND status != 'sent'",
        [to_iso(now), *ids],
    )
    return cur.rowcount


def mark_failed(conn: sqlite3.Connection, alert_id: int) -> str | None:
    """Count a failed delivery; the alert gives up after MAX_DELIVERY_ATTEMPTS."""
    row = conn.execute(
        "SELECT status, delivery_attempts FROM alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if row is None:
        return None
    if row["status"] == "sent":
        return "sent"
    attempts = row["delivery_attempts"] + 1
    status = "failed" if attempts >= MAX_DELIVERY_ATTEMPTS else "pending"
    conn.execute(
        "UPDATE alerts SET delivery_attempts = ?, status = ? WHERE id = ?",
        (attempts, status, alert_id),
    )
    return status


def dispatch_immediate(
    conn: sqlite3.Connection,
    alert_id: int,
    *,
    url: str,
    token: str,
    cfg: Mapping[str, Any] | None = None,
    post: Callable[..., Any] = httpx.post,
) -> bool:
    """POST the alert to the n8n webhook. On failure it stays pending for the polling route.

    n8n marks it sent through the internal API once Telegram accepted the message, which
    is the moment used for the submission-to-alert time.
    """
    cfg = cfg or config.settings()
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    payload = view_payload(load_views(conn, [row], cfg)[0], cfg)
    try:
        response = post(url, json=payload, headers={"X-Internal-Token": token},
                        timeout=WEBHOOK_TIMEOUT_S)
        response.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL, OSError):
        conn.execute(
            "UPDATE alerts SET delivery_attempts = delivery_attempts + 1 WHERE id = ?",
            (alert_id,),
        )
        return False
    return True


def env_notifier(
    cfg: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None
) -> Notifier | None:
    """Webhook notifier from N8N_ALERT_WEBHOOK_URL and INTERNAL_API_TOKEN, if configured."""
    env = os.environ if env is None else env
    url = env.get("N8N_ALERT_WEBHOOK_URL", "")
    if not url:
        return None
    return partial(dispatch_immediate, url=url, token=env.get("INTERNAL_API_TOKEN", ""), cfg=cfg)
