import copy
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from enps import alerts, config
from enps.classify.types import ClassificationResult
from enps.storage import db
from enps.storage.timeutil import to_iso

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def cfg():
    return copy.deepcopy(config.settings())


@pytest.fixture
def conn(tmp_path):
    connection = db.open_db(tmp_path / "alerts.db")
    yield connection
    connection.close()


def critical_response(conn, *, area="atendimento", dataset="public", comment="me humilham",
                      types=("am",)):
    rid = db.insert_response(conn, {
        "public_id": secrets.token_hex(5), "dataset": dataset,
        "channel": "site" if dataset == "public" else "synthetic",
        "submitted_at": to_iso(NOW - timedelta(minutes=1)), "score": 1, "area": area,
        "tenure": "lt1", "comment_anon": comment, "anonymizer_version": "t", "status": "done",
    })
    db.save_classification(conn, rid, ClassificationResult(
        status="done", themes=["lid", "car"], sentiment="neg", critical=True,
        critical_types=list(types)), to_iso(NOW))
    return conn.execute("SELECT * FROM responses WHERE id = ?", (rid,)).fetchone()


def new_alert(conn, cfg, now=NOW, **kwargs):
    row = critical_response(conn, **kwargs)
    return alerts.create_alert(conn, row, list(kwargs.get("types", ("am",))), now, cfg)


def alert_row(conn, alert_id):
    return conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()


def test_no_alert_outside_configured_datasets(conn, cfg):
    assert new_alert(conn, cfg, dataset="synthetic") is None
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 0


def test_mode_switches_to_digest_after_hourly_limit(conn, cfg):
    modes = [new_alert(conn, cfg, now=NOW + timedelta(minutes=i)).mode for i in range(5)]
    assert modes == ["immediate"] * 3 + ["digest"] * 2
    later = new_alert(conn, cfg, now=NOW + timedelta(minutes=63))
    assert later.mode == "immediate"


def test_message_escapes_html_and_hides_small_area(conn, cfg):
    created = new_alert(conn, cfg, comment="<script>x</script> & seu porra", types=("am", "bo"))
    view = alerts.load_views(conn, [alert_row(conn, created.id)], cfg)[0]
    message = alerts.message_html(view, cfg)
    assert message.startswith("<b>Caso crítico na pesquisa de clima</b>")
    assert "Tipos: Assédio moral, Burnout e saúde mental" in message
    assert "Temas: Liderança, Carga de trabalho" in message
    assert "Área omitida (grupo pequeno)" in message and "Atendimento" not in message
    assert "&lt;script&gt;x&lt;/script&gt; &amp; seu [ofensivo]" in message
    assert "<script>" not in message
    assert 'href="https://voz-sem-nome.andreiludescher.com/painel/#criticos"' in message
    assert "\u2014" not in message and "\u2013" not in message and " - " not in message


def test_message_shows_area_when_group_is_visible(conn, cfg):
    for _ in range(4):
        critical_response(conn, area="operacoes")
    created = new_alert(conn, cfg, area="operacoes")
    view = alerts.load_views(conn, [alert_row(conn, created.id)], cfg)[0]
    payload = alerts.view_payload(view, cfg)
    assert payload["area"] == "operacoes"
    assert "Área: Operações" in payload["message_html"]


def test_digest_counts_types_and_keeps_five_excerpts(conn, cfg):
    rows = []
    for i in range(7):
        created = new_alert(conn, cfg, comment=f"caso {i}", types=("am",) if i % 2 else ("bo",))
        rows.append(alert_row(conn, created.id))
    digest = alerts.build_digest(alerts.load_views(conn, rows, cfg), cfg)
    assert digest["count"] == 7 and digest["by_type"] == {"am": 3, "bo": 4}
    assert len(digest["ids"]) == 7
    html = digest["message_html"]
    assert "7 casos novos." in html and "Mais 2 no painel." in html
    assert "caso 4" in html and "caso 5" not in html


def test_pending_payload_timing(conn, cfg):
    immediate = new_alert(conn, cfg)
    for i in range(3):
        new_alert(conn, cfg, now=NOW + timedelta(minutes=i))  # third one onward is digest
    payload = alerts.pending_payload(conn, NOW + timedelta(minutes=1), cfg)
    assert payload["immediate"] == [] and payload["digest"] is None
    payload = alerts.pending_payload(conn, NOW + timedelta(minutes=3), cfg)
    assert [a["alert_id"] for a in payload["immediate"]][0] == immediate.id
    assert payload["digest"] is None and payload["digest_waiting"] == 1
    payload = alerts.pending_payload(conn, NOW + timedelta(minutes=33), cfg)
    assert payload["digest"]["count"] == 1
    alerts.mark_sent(conn, payload["digest"]["ids"], NOW + timedelta(minutes=34))
    assert alerts.pending_payload(conn, NOW + timedelta(minutes=35), cfg)["digest"] is None


def test_dispatch_posts_payload_and_keeps_failures_pending(conn, cfg):
    created = new_alert(conn, cfg)
    calls = []

    def ok_post(url, *, json, headers, timeout):
        calls.append((url, json, headers))
        return httpx.Response(200, request=httpx.Request("POST", url))

    assert alerts.dispatch_immediate(conn, created.id, url="http://n8n/hook", token="tok",
                                     cfg=cfg, post=ok_post)
    url, body, headers = calls[0]
    assert headers == {"X-Internal-Token": "tok"} and body["alert_id"] == created.id
    assert set(body) >= {"dataset", "types", "themes", "area", "excerpt", "submitted_at",
                         "detected_at", "message_html"}

    def failing_post(url, **_):
        raise httpx.ConnectError("down")

    assert not alerts.dispatch_immediate(conn, created.id, url="http://n8n/hook", token="tok",
                                         cfg=cfg, post=failing_post)
    row = alert_row(conn, created.id)
    assert row["status"] == "pending" and row["delivery_attempts"] == 1


def test_env_notifier_needs_webhook_url(cfg):
    assert alerts.env_notifier(cfg, env={}) is None
    assert alerts.env_notifier(cfg, env={"N8N_ALERT_WEBHOOK_URL": "http://n8n/x"}) is not None


def test_mark_failed_and_sent(conn, cfg):
    created = new_alert(conn, cfg)
    assert [alerts.mark_failed(conn, created.id) for _ in range(3)] == [
        "pending", "pending", "failed"]
    assert alerts.mark_failed(conn, 999) is None
    other = new_alert(conn, cfg)
    assert alerts.mark_sent(conn, [other.id], NOW) == 1
    assert alerts.mark_sent(conn, [other.id], NOW) == 0
    assert alerts.mark_failed(conn, other.id) == "sent"


def test_dispatch_keeps_alert_pending_on_invalid_url(conn, cfg):
    created = new_alert(conn, cfg)
    assert not alerts.dispatch_immediate(conn, created.id, url="http://[::1", token="tok",
                                         cfg=cfg)
    row = alert_row(conn, created.id)
    assert row["status"] == "pending" and row["delivery_attempts"] == 1
