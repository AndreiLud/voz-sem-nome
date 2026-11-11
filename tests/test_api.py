import copy
import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import enps.anonymize
from enps import config
from enps.anonymize import AnonymizationResult
from enps.api import internal, public
from enps.classify.types import ClassificationResult, StageRecord
from enps.queue.worker import Worker
from enps.storage import db

START = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
ENV = {"ENPS_ENV": "prod", "ORIGIN_SHARED_SECRET": "origin-secret",
       "INTERNAL_API_TOKEN": "internal-token"}
IP = "203.0.113.5"
SUBMIT = "/v1/public/submissions"
_numbers = itertools.count()


class FakeClock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture(autouse=True)
def fake_anonymizer(monkeypatch):
    def anonymize(text):
        return AnonymizationResult(text=text.replace("João Silva", "[PESSOA]"),
                                   counts={"PESSOA": 1} if "João Silva" in text else {},
                                   version="anon-test")
    monkeypatch.setattr(enps.anonymize, "anonymize", anonymize)


@pytest.fixture
def cfg():
    return copy.deepcopy(config.settings())


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "app.db"


@pytest.fixture
def make_public(db_path, tmp_path, cfg, clock):
    def factory(env=ENV):
        app = public.create_app(db_path=db_path, pipeline_db_path=tmp_path / "missing.db",
                                cfg=cfg, clock=clock, env=env)
        return TestClient(app)
    return factory


@pytest.fixture
def client(make_public):
    return make_public()


@pytest.fixture
def internal_client(db_path, cfg, clock):
    return TestClient(internal.create_app(db_path=db_path, cfg=cfg, clock=clock, env=ENV))


def post(client, *, ip=IP, secret="origin-secret", **fields):
    body = {"score": 8, "area": "operacoes", "tenure": "lt1", "lang": "pt", "consent": True,
            "comment": f"Comentário de teste número {next(_numbers)}", **fields}
    headers = {"X-Client-IP": ip}
    if secret is not None:
        headers["X-Origin-Secret"] = secret
    return client.post(SUBMIT, json=body, headers=headers)


def run_worker(db_path, cfg, clock, *, critical=False):
    def classify(text):
        clock.advance(seconds=4)
        return ClassificationResult(
            status="done", themes=["lid"], sentiment="neg", critical=critical,
            critical_types=["am"] if critical else [], evidence=text[:15], model="fake-2b",
            stages=[StageRecord(name="classify", total_ms=4000)])
    conn = db.open_db(db_path)
    try:
        return Worker(conn, classify=classify, cfg=cfg, clock=clock, poll_s=0).run(drain=True)
    finally:
        conn.close()


def stats(db_path, column):
    conn = db.connect(db_path)
    try:
        return conn.execute(f"SELECT coalesce(sum({column}), 0) FROM daily_stats").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------- public API


def test_submit_and_track_result(client, db_path, cfg, clock):
    response = post(client, comment="O João Silva grita com todo mundo")
    assert response.status_code == 202
    body = response.json()
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", body["code"])
    assert body["status"] == "queued" and body["queue_position"] == 1
    assert body["eta_seconds"] == 15
    assert body["comment_anon"] == "O [PESSOA] grita com todo mundo"
    assert body["pii_counts"] == {"PESSOA": 1}

    waiting = client.get(f"{SUBMIT}/{body['code']}").json()
    assert waiting["status"] == "queued" and waiting["queue_position"] == 1

    run_worker(db_path, cfg, clock)
    done = client.get(f"{SUBMIT}/{body['code'].replace('-', '').lower()}").json()
    assert done["status"] == "done" and done["themes"] == ["lid"]
    assert done["sentiment"] == "neg" and done["critical"] is False
    assert done["critical_types"] == [] and done["evidence"] == "O [PESSOA] grit"
    assert done["model"] == "fake-2b" and done["processing_seconds"] == 4.0
    assert client.get(f"{SUBMIT}/ABCD-EFGH").status_code == 404
    assert client.get(f"{SUBMIT}/not-a-code").status_code == 404


def test_empty_comment_is_skipped(client):
    body = post(client, comment="").json()
    assert body["status"] == "skipped" and body["queue_position"] is None
    assert client.get(f"{SUBMIT}/{body['code']}").json()["status"] == "skipped"


def test_origin_secret(make_public):
    client = make_public()
    assert post(client, secret=None).status_code == 403
    assert post(client, secret="wrong").status_code == 403
    no_secret = {"ENPS_ENV": "prod"}
    assert post(make_public(no_secret), secret=None).status_code == 403
    assert post(make_public({"ENPS_ENV": "dev"}), secret=None).status_code == 202


def test_validation_errors(client):
    response = post(client, score=11, area="Operações", consent=False)
    assert response.status_code == 422
    assert set(response.json()["fields"]) == {"score", "area", "consent"}
    assert post(client, comment="x" * 1001).json()["fields"] == {
        "comment": "must have at most 1000 characters"}
    raw = client.post(SUBMIT, content=b"{not json", headers={
        "X-Origin-Secret": "origin-secret", "Content-Type": "application/json"})
    assert raw.status_code == 422 and raw.json()["error"] == "validation"
    too_big = client.post(SUBMIT, content=b"x" * 20_000,
                          headers={"X-Origin-Secret": "origin-secret"})
    assert too_big.status_code == 413


def test_duplicate_is_rejected(client, db_path):
    assert post(client, comment="Minha gestora é ótima comigo").status_code == 202
    response = post(client, ip="198.51.100.9", comment="minha gestora é ÓTIMA comigo!")
    assert response.status_code == 409 and response.json() == {"error": "duplicate"}
    assert post(client, comment="ok").status_code == 202
    assert post(client, comment="ok", ip="198.51.100.10").status_code == 202  # too short to dedupe
    assert stats(db_path, "rejected_duplicate") == 1


def test_per_ip_window_limit(client, db_path, clock):
    assert [post(client).status_code for _ in range(3)] == [202, 202, 202]
    blocked = post(client)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "600"
    assert blocked.json()["scope"] == "ip_window"
    assert post(client, ip="198.51.100.7").status_code == 202
    clock.advance(seconds=601)
    assert post(client).status_code == 202
    assert stats(db_path, "rejected_rate_limit") == 1


def test_per_ip_day_and_global_limits(make_public, cfg, clock):
    cfg["limits"].update(per_ip_window_max=100, per_ip_day_max=4, global_hour_max=6)
    client = make_public()
    assert all(post(client).status_code == 202 for _ in range(4))
    day_block = post(client)
    assert day_block.status_code == 429 and day_block.json()["scope"] == "ip_day"
    assert int(day_block.headers["Retry-After"]) == 86_400
    assert post(client, ip="198.51.100.1").status_code == 202
    assert post(client, ip="198.51.100.2").status_code == 202
    global_block = post(client, ip="198.51.100.3")
    assert global_block.status_code == 429 and global_block.json()["scope"] == "global_hour"
    clock.advance(hours=1, seconds=1)
    assert post(client, ip="198.51.100.3").status_code == 202


def test_queue_full(make_public, cfg, db_path):
    cfg["queue"]["max_pending"] = 2
    client = make_public()
    assert post(client).status_code == 202
    assert post(client, ip="198.51.100.1").status_code == 202
    full = post(client, ip="198.51.100.2")
    assert full.status_code == 503 and full.json() == {"error": "queue_full"}
    assert "Retry-After" in full.headers
    assert stats(db_path, "rejected_queue_full") == 1


def test_distinct_submitters_without_storing_ips(client, db_path):
    post(client)
    post(client)
    post(client, ip="198.51.100.20")
    assert stats(db_path, "distinct_submitters") == 2
    assert stats(db_path, "submissions") == 3
    stored = b"".join(p.read_bytes() for p in db_path.parent.glob("app.db*"))
    assert IP.encode() not in stored and b"198.51.100.20" not in stored


def test_dashboard_is_cached(client, clock):
    for i in range(5):
        assert post(client, ip=f"198.51.100.{i}", score=10).status_code == 202
    first = client.get("/v1/public/dashboard?dataset=public").json()
    assert first["dataset"] == "public" and first["totals"]["responses"] == 5
    assert first["totals"]["enps"] == 100
    post(client, ip="198.51.100.99")
    assert client.get("/v1/public/dashboard").json()["totals"]["responses"] == 5
    clock.advance(seconds=31)
    assert client.get("/v1/public/dashboard").json()["totals"]["responses"] == 6
    synthetic = client.get("/v1/public/dashboard?dataset=synthetic").json()
    assert synthetic["dataset"] == "synthetic" and synthetic["totals"]["responses"] is None
    assert client.get("/v1/public/dashboard?dataset=other").status_code == 422


def test_status_and_health(client, db_path, cfg, clock):
    assert client.get("/v1/public/health").json() == {"status": "ok"}
    post(client)
    post(client, ip="198.51.100.1")
    status = client.get("/v1/public/status").json()
    assert status["queue"]["pending"] == 2 and status["queue"]["eta_seconds_new_job"] == 45
    assert status["worker"]["heartbeat_age_seconds"] is None
    run_worker(db_path, cfg, clock)
    clock.advance(seconds=10)
    status = client.get("/v1/public/status").json()
    assert status["worker"]["heartbeat_age_seconds"] == 10.0
    assert status["queue"]["average_job_seconds"] == 4.0
    production = status["production"]
    assert production["total_submissions"] == 2 and production["distinct_submitters"] == 2
    assert production["classified"] == 2 and production["first_public_day"] == "2026-11-11"
    assert production["latency_p50_seconds"] == 4.0 and production["latency_p95_seconds"] == 8.0
    assert len(status["queue_series"]) == 1


def test_docs_only_in_dev(make_public):
    prod = make_public()
    assert prod.get("/docs").status_code == 404
    assert prod.get("/openapi.json").status_code == 404
    dev = make_public({"ENPS_ENV": "dev"})
    assert dev.get("/openapi.json").status_code == 200


# ---------------------------------------------------------------- internal API

TOKEN = {"X-Internal-Token": "internal-token"}


def test_internal_requires_token(internal_client):
    assert internal_client.get("/v1/internal/health").status_code == 401
    wrong = internal_client.get("/v1/internal/health", headers={"X-Internal-Token": "x"})
    assert wrong.status_code == 401 and wrong.json() == {"error": "unauthorized"}
    assert internal_client.get("/v1/internal/health", headers=TOKEN).json() == {"status": "ok"}
    assert internal_client.post("/v1/internal/maintenance/purge").status_code == 401


def test_internal_google_forms_row(internal_client, db_path):
    row = {"score": "6", "area": "Pessoas e Cultura", "tenure": "De 1 a 3 anos",
           "comment": "Falta reconhecimento na área", "submitted_at": "11/11/2026 11:00:00"}
    response = internal_client.post("/v1/internal/responses", json=row, headers=TOKEN)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued" and len(body["public_id"]) == 10
    conn = db.connect(db_path)
    stored = db.response_by_public_id(conn, body["public_id"])
    conn.close()
    assert (stored["channel"], stored["dataset"], stored["area"], stored["tenure"]) == (
        "google_forms", "public", "pessoas", "y1to3")
    assert stored["submitted_at"] == "2026-11-11T14:00:00.000+00:00"
    again = internal_client.post("/v1/internal/responses", json=row, headers=TOKEN)
    assert again.status_code == 409
    invalid = internal_client.post("/v1/internal/responses", json={"score": 12}, headers=TOKEN)
    assert invalid.status_code == 422


def test_internal_alert_flow(client, internal_client, db_path, cfg, clock):
    post(client, comment="Meu gestor me humilha na frente de todos")
    run_worker(db_path, cfg, clock, critical=True)
    pending = internal_client.get("/v1/internal/alerts/pending", headers=TOKEN).json()
    assert pending == {"immediate": [], "digest": None, "digest_waiting": 0, "channels": []}
    clock.advance(minutes=3)
    pending = internal_client.get("/v1/internal/alerts/pending", headers=TOKEN).json()
    alert = pending["immediate"][0]
    assert alert["types"] == ["am"] and alert["area"] is None
    assert "Caso crítico na pesquisa de clima" in alert["message_html"]
    sent = internal_client.post(f"/v1/internal/alerts/{alert['alert_id']}/sent", headers=TOKEN)
    assert sent.json() == {"id": alert["alert_id"], "status": "sent"}
    production = client.get("/v1/public/status").json()["production"]
    assert production["alerts_sent"] == 1
    assert production["mean_submission_to_alert_seconds"] == 184.0
    assert internal_client.post("/v1/internal/alerts/999/sent", headers=TOKEN).status_code == 404
    assert internal_client.post("/v1/internal/alerts/999/failed", headers=TOKEN).status_code == 404
    failed = internal_client.post(f"/v1/internal/alerts/{alert['alert_id']}/failed", headers=TOKEN)
    assert failed.json()["status"] == "sent"
    bad = internal_client.post("/v1/internal/alerts/sent", json={"ids": []}, headers=TOKEN)
    assert bad.status_code == 422
    digest = internal_client.post("/v1/internal/alerts/sent", json={"ids": [alert["alert_id"]]},
                                  headers=TOKEN)
    assert digest.json() == {"updated": 0}


def test_internal_purge_and_daily_summary(client, internal_client, clock):
    post(client)
    post(client, comment="")
    summary = internal_client.get("/v1/internal/summary/daily", headers=TOKEN).json()
    assert summary["day"] == "2026-11-11" and summary["submissions"] == 2
    assert summary["message_html"].startswith("<b>Resumo do dia 11/11/2026</b>")
    assert "\u2014" not in summary["message_html"]
    clock.advance(days=31)
    purged = internal_client.post("/v1/internal/maintenance/purge", headers=TOKEN).json()
    assert purged == {"responses": 2, "alerts": 0, "queue_samples": 0}


def test_record_rechecks_limits_for_concurrent_requests(cfg, clock):
    limiter = public.RateLimiter(public.Limits.from_settings(cfg), clock)
    for _ in range(4):
        limiter.check(IP)  # four requests pass the first check before any is recorded
    for _ in range(3):
        limiter.record(IP)
    with pytest.raises(public.RateLimited) as blocked:
        limiter.record(IP)
    assert blocked.value.scope == "ip_window"


def test_slot_taken_by_a_concurrent_request_is_rejected(client, db_path, monkeypatch):
    limiter = client.app.state.public.limiter
    for _ in range(3):
        limiter.record(IP)
    monkeypatch.setattr(limiter, "check", lambda ip: None)  # passed before the others landed
    blocked = post(client)
    assert blocked.status_code == 429 and blocked.json()["scope"] == "ip_window"
    assert stats(db_path, "rejected_rate_limit") == 1 and stats(db_path, "submissions") == 0
