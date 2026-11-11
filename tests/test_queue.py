import copy
import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import enps.anonymize
from enps import config
from enps.anonymize import AnonymizationResult
from enps.classify.types import ClassificationResult, StageRecord
from enps.queue import eta, ingest
from enps.queue.worker import Worker
from enps.storage import db
from enps.storage.timeutil import to_iso

START = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def fake_anonymize(text):
    return AnonymizationResult(text=text.replace("João Silva", "[PESSOA]"),
                               counts={"PESSOA": 1} if "João Silva" in text else {},
                               version="anon-test")


@pytest.fixture(autouse=True)
def anonymizer(monkeypatch):
    calls = []

    def spy(text):
        calls.append(text)
        return fake_anonymize(text)

    monkeypatch.setattr(enps.anonymize, "anonymize", spy)
    return calls


@pytest.fixture
def cfg():
    return copy.deepcopy(config.settings())


@pytest.fixture
def conn(tmp_path):
    connection = db.open_db(tmp_path / "queue.db")
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return FakeClock()


def submit(conn, cfg, clock, comment, *, channel="site", score=9, area="operacoes"):
    sub = ingest.Submission(score=score, area=area, tenure="lt1", comment=comment)
    return ingest.ingest(conn, sub, channel=channel, cfg=cfg, now=clock())


def result(text, *, critical=False, status="done"):
    return ClassificationResult(
        status=status, themes=["lid"], sentiment="neg", critical=critical,
        critical_types=["am"] if critical else [], critical_sources=["llm"] if critical else [],
        evidence=text[:20], model="fake", quantization="Q4_K_M", prompt_version="classify.v1",
        stages=[StageRecord(name="classify", total_ms=3000, output_tokens=12, prompt_tokens=300)],
    )


def classifier(clock, seconds=3, **kwargs):
    def classify(text):
        clock.advance(seconds=seconds)
        return result(text, **kwargs)
    return classify


# ---------------------------------------------------------------- ingest


def test_dedupe_key_normalization():
    assert ingest.normalize_comment("  Meu GESTOR, [PESSOA]!!  é   ótimo...") == \
        "meu gestor pessoa é ótimo"
    assert ingest.dedupe_key("Meu gestor é ótimo demais!") == \
        ingest.dedupe_key("meu gestor  é ótimo, demais")
    assert ingest.dedupe_key("Meu gestor é otimo demais") != ingest.dedupe_key(
        "Meu gestor é ótimo demais")  # accents are kept
    assert ingest.dedupe_key("tudo certo!") is None  # fewer than 15 characters


def test_tracking_code_and_public_id():
    code = ingest.new_tracking_code()
    assert re.fullmatch(r"[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", code)
    assert not set(code) & set("0O1IL")
    raw = code.replace("-", "")
    assert ingest.tracking_hash(raw.lower()) == ingest.tracking_hash(code)
    assert ingest.format_tracking_code(raw.lower()) == code
    assert ingest.tracking_hash("ABCD-EFG0") is None and ingest.tracking_hash("ABC") is None
    public_id = ingest.new_public_id()
    assert len(public_id) == 10 and re.fullmatch(r"[A-Za-z0-9_-]+", public_id)


def test_form_row_accepts_labels_and_local_timestamps(cfg):
    sub = ingest.validate_form_row(
        {"score": "7", "area": "pessoas e cultura", "tenure": "Mais de 5 anos",
         "comment": "  ok  ", "submitted_at": "11/11/2026 10:30:00"}, cfg, START)
    assert (sub.score, sub.area, sub.tenure, sub.comment) == (7, "pessoas", "gt5", "ok")
    assert to_iso(sub.submitted_at) == "2026-11-11T13:30:00.000+00:00"
    with pytest.raises(ingest.InvalidSubmission) as err:
        ingest.validate_form_row({"score": 7, "area": "Marte", "tenure": "gt5",
                                  "submitted_at": "2026-11-12T10:00:00-03:00"}, cfg, START)
    assert set(err.value.fields) == {"area", "submitted_at"}


def test_site_validation_is_strict(cfg):
    base = {"score": 7, "area": "operacoes", "tenure": "lt1", "consent": True}
    assert ingest.validate_site(base, cfg).lang == "pt"
    bad = {"score": True, "area": "Operações", "tenure": "x", "comment": "a" * 1001,
           "lang": "es", "consent": "yes"}
    with pytest.raises(ingest.InvalidSubmission) as err:
        ingest.validate_site(bad, cfg)
    assert set(err.value.fields) == {"score", "area", "tenure", "comment", "lang", "consent"}
    with pytest.raises(ingest.InvalidSubmission):
        ingest.validate_site({**base, "score": "7"}, cfg)
    with pytest.raises(ingest.InvalidSubmission):
        ingest.validate_site([base], cfg)


def test_empty_comment_is_skipped_without_anonymizer(conn, cfg, clock, anonymizer):
    accepted = submit(conn, cfg, clock, "")
    row = db.response_by_public_id(conn, accepted.public_id)
    assert accepted.status == "skipped" and anonymizer == []
    assert row["queued_at"] is None and row["finished_at"] is not None
    assert db.claim_next_job(conn, to_iso(clock())) is None


def test_duplicates_are_rejected_inside_the_window(conn, cfg, clock):
    first = submit(conn, cfg, clock, "O João Silva grita com a equipe")
    assert first.comment_anon == "O [PESSOA] grita com a equipe"
    assert first.pii_counts == {"PESSOA": 1} and first.tracking_code
    with pytest.raises(ingest.DuplicateSubmission):
        submit(conn, cfg, clock, "o João Silva GRITA com a equipe!!", channel="google_forms")
    clock.advance(hours=25)
    submit(conn, cfg, clock, "O João Silva grita com a equipe")
    stats = conn.execute("SELECT sum(rejected_duplicate) AS d, sum(submissions) AS s "
                         "FROM daily_stats").fetchone()
    assert (stats["d"], stats["s"]) == (1, 2)


def test_synthetic_batch_ingest(conn, cfg, tmp_path):
    path = tmp_path / "responses.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "submitted_at", "score", "area", "tenure", "comment"])
        writer.writerow(["S0001", "2026-10-01T09:15:00-03:00", "9", "operacoes", "lt1",
                         "Falei com João Silva, ótimo"])
        writer.writerow(["S0002", "2026-10-02T18:00:00-03:00", "3", "juridico", "gt5", ""])
        writer.writerow(["S0003", "2026-10-02T18:05:00-03:00", "3", "juridico", "gt5",
                         "Falei com João Silva, ótimo"])
    report = ingest.ingest_synthetic_csv(conn, path, cfg=cfg)
    assert (report.inserted, report.already_present) == (3, 0)
    rows = conn.execute("SELECT * FROM responses ORDER BY id").fetchall()
    assert [r["source_ref"] for r in rows] == ["S0001", "S0002", "S0003"]
    assert rows[0]["submitted_at"] == "2026-10-01T12:15:00.000+00:00"
    assert {r["dataset"] for r in rows} == {"synthetic"} and rows[0]["priority"] == 10
    assert [r["status"] for r in rows] == ["queued", "skipped", "queued"]
    assert rows[0]["tracking_hash"] is None
    assert ingest.ingest_synthetic_csv(conn, path, cfg=cfg).already_present == 3


# ---------------------------------------------------------------- eta


def test_eta_uses_moving_average(conn, cfg, clock):
    a = submit(conn, cfg, clock, "primeiro comentário longo")
    b = submit(conn, cfg, clock, "segundo comentário longo")
    assert eta.average_job_seconds(conn, 20) == eta.DEFAULT_JOB_SECONDS
    job_b = db.response_by_public_id(conn, b.public_id)
    assert eta.job_position(conn, job_b) == 2
    db.claim_next_job(conn, to_iso(clock()))
    assert eta.job_position(conn, db.response_by_public_id(conn, a.public_id)) == 1
    assert eta.job_position(conn, job_b) == 2  # one processing ahead
    for ms in (4000, 6000):
        db.add_latency_sample(conn, day="2026-11-11", dataset="public", channel="site",
                              queue_ms=0, processing_ms=ms)
    assert eta.average_job_seconds(conn, 20) == 5.0
    assert eta.eta_seconds(eta.new_job_position(conn, 0), 5.0) == 15


# ---------------------------------------------------------------- worker


def test_worker_processes_by_priority_and_records_everything(conn, cfg, clock):
    synthetic = ingest.ingest(conn, ingest.Submission(5, "operacoes", "lt1", "lote sintético"),
                              channel="synthetic", cfg=cfg, now=clock(), source_ref="S0001")
    live = submit(conn, cfg, clock, "comentário do site")
    clock.advance(seconds=2)
    seen = []
    out = io.StringIO()

    def classify(text):
        seen.append(text)
        clock.advance(seconds=3)
        return result(text, status="review" if "lote" in text else "done")

    done = Worker(conn, classify=classify, cfg=cfg, clock=clock, poll_s=0, out=out).run(drain=True)
    assert done == 2 and seen == ["comentário do site", "lote sintético"]
    statuses = {r["public_id"]: r["status"] for r in conn.execute("SELECT * FROM responses")}
    assert statuses == {live.public_id: "done", synthetic.public_id: "review"}
    row = db.classification_for(conn, db.response_by_public_id(conn, live.public_id)["id"])
    assert json.loads(row["themes"]) == ["lid"] and row["model"] == "fake"
    samples = conn.execute("SELECT * FROM latency_samples ORDER BY id").fetchall()
    assert [(s["queue_ms"], s["processing_ms"]) for s in samples] == [(2000, 3000), (5000, 3000)]
    stats = conn.execute("SELECT * FROM daily_stats WHERE channel = 'site'").fetchone()
    assert (stats["submissions"], stats["classified"], stats["critical"]) == (1, 1, 0)
    assert db.get_meta(conn, db.WORKER_HEARTBEAT_KEY) == to_iso(clock())
    assert conn.execute("SELECT count(*) FROM queue_samples").fetchone()[0] == 1
    assert out.getvalue().splitlines()[1].startswith("[2/2] S0001 review")


def test_worker_retries_then_fails(conn, cfg, clock):
    submit(conn, cfg, clock, "comentário que quebra")

    def broken(_):
        raise RuntimeError("model down")

    worker = Worker(conn, classify=broken, cfg=cfg, clock=clock, poll_s=0)
    assert worker.run(drain=True) == 2  # max_attempts is 2
    row = conn.execute("SELECT status, attempts FROM responses").fetchone()
    assert (row["status"], row["attempts"]) == ("failed", 2)
    assert conn.execute("SELECT count(*) FROM classifications").fetchone()[0] == 0


def test_worker_resets_stale_jobs_and_handles_skipped(conn, cfg, clock):
    accepted = submit(conn, cfg, clock, "   texto   ")
    db.claim_next_job(conn, to_iso(clock()))  # left in processing by a crash
    worker = Worker(conn, classify=classifier(clock, status="skipped"), cfg=cfg, clock=clock,
                    poll_s=0)
    assert worker.run(drain=True) == 1
    assert db.response_by_public_id(conn, accepted.public_id)["status"] == "skipped"
    assert conn.execute("SELECT count(*) FROM classifications").fetchone()[0] == 0


def test_critical_result_creates_alert_and_notifies(conn, cfg, clock):
    submit(conn, cfg, clock, "sofro assédio do gestor")
    ingest.ingest(conn, ingest.Submission(1, "operacoes", "lt1", "assédio no lote"),
                  channel="synthetic", cfg=cfg, now=clock(), source_ref="S0009")
    notified = []
    worker = Worker(conn, classify=classifier(clock, critical=True), cfg=cfg, clock=clock,
                    notifier=lambda c, alert_id: notified.append(alert_id) or True, poll_s=0)
    worker.run(drain=True)
    rows = conn.execute("SELECT * FROM alerts").fetchall()
    assert len(rows) == 1 and rows[0]["dataset"] == "public"
    assert rows[0]["mode"] == "immediate" and notified == [rows[0]["id"]]
    assert rows[0]["detected_at"] == to_iso(START + timedelta(seconds=3))
    stats = conn.execute("SELECT sum(critical) FROM daily_stats").fetchone()[0]
    assert stats == 2


def test_worker_stops_after_current_job(conn, cfg, clock):
    for text in ("primeiro comentário", "segundo comentário"):
        submit(conn, cfg, clock, text)
    worker = None

    def classify(text):
        worker.stop()  # SIGTERM arrives while the model is working
        return result(text)

    worker = Worker(conn, classify=classify, cfg=cfg, clock=clock, poll_s=0)
    assert worker.run() == 1
    assert db.queue_counts(conn) == {"pending": 1, "processing": 0}


def test_worker_survives_a_failing_notifier(conn, cfg, clock):
    submit(conn, cfg, clock, "sofro assédio do gestor")
    submit(conn, cfg, clock, "outro relato de assédio", area="transporte")

    def broken(_conn, _alert_id):
        raise ValueError("bad webhook url")

    worker = Worker(conn, classify=classifier(clock, critical=True), cfg=cfg, clock=clock,
                    notifier=broken, poll_s=0)
    assert worker.run(drain=True) == 2
    assert {r["status"] for r in conn.execute("SELECT status FROM responses")} == {"done"}
    assert [r["status"] for r in conn.execute("SELECT status FROM alerts")] == ["pending"] * 2
