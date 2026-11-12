import csv
import json
from datetime import datetime, timezone

import httpx
import pytest

from enps.evaluation import edge_client as ec

BASE = "https://edge.example.test"
TOKEN = "eval-secret"
GEMMA = "@cf/google/gemma-4-26b-a4b-it"
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def data(tmp_path):
    """25 responses (every fifth without comment), splits and intended labels."""
    rows = [{"id": f"S{i:04d}", "submitted_at": "2026-10-19T08:16:14-03:00", "score": str(i % 11),
             "area": "operacoes", "tenure": "lt1", "comment": "" if i % 5 == 0 else f"comentário {i} sobre a escala"}
            for i in range(1, 26)]
    responses = tmp_path / "responses.csv"
    with open(responses, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with_comment = [r["id"] for r in rows if r["comment"]]
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"seed": 1, "test": with_comment[:12], "dev": with_comment[12:],
                                  "dev_quick": with_comment[12:16]}), encoding="utf-8")
    intended = tmp_path / "intended_labels.csv"
    with open(intended, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "themes", "sentiment", "critical", "critical_types", "subtle", "traits"])
        for r in rows:
            if not r["comment"]:
                writer.writerow([r["id"], "", "", "0", "", "0", "empty"])
            else:
                n = int(r["id"][1:])
                critical = n % 4 == 0
                writer.writerow([r["id"], "inf", "neg", "1" if critical else "0", "am" if critical else "", "0", ""])
    return {"responses": responses, "splits": splits, "intended": intended, "out": tmp_path / "edge",
            "comments": ec.read_comments(responses)}


class FakeWorker:
    """httpx transport answering like POST /api/v1/internal/evaluate."""

    def __init__(self, *, quota_after=None, status=200, fail_ids=(), work_ms=2000):
        self.requests = []
        self.quota_after = quota_after
        self.status = status
        self.fail_ids = set(fail_ids)
        self.work_ms = work_ms
        self.answered = 0

    def item(self, item):
        n = int(item["id"][1:])
        if item["id"] in self.fail_ids:
            result = {"status": "failed", "error_code": 5028}
        else:
            critical = n % 4 == 0
            stages = [
                {"name": "lexicon", "ok": True, "error": None, "output": {"hits": []}, "prompt_tokens": 0,
                 "output_tokens": 0, "total_ms": 0},
                {"name": "classify", "ok": True, "error": None,
                 "output": {"t": ["inf"], "s": "neg", "c": critical, "k": ["am"] if critical else [], "e": ""},
                 "prompt_tokens": 2100, "output_tokens": 40, "total_ms": self.work_ms - 5},
            ]
            result = {"status": "done", "themes": ["inf"], "sentiment": "neg", "critical": critical,
                      "critical_types": ["am"] if critical else [], "critical_sources": ["llm"] if critical else [],
                      "evidence": None, "model": GEMMA, "prompt_version": "classify.v3+708a3315",
                      "verifier_version": None, "stages": stages, "ai_ms": self.work_ms - 5,
                      "prompt_tokens": 2100, "output_tokens": 40}
        return {"id": item["id"], "comment_anon": item["comment"], "pii_counts": {}, "anonymize_ms": 0,
                "result": result, "total_ms": self.work_ms}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "headers": request.headers, "body": body})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        items = []
        for item in body["items"]:
            if self.quota_after is not None and self.answered >= self.quota_after:
                return httpx.Response(503, json={"error": "quota", "model": GEMMA, "anonymizer_version": "edge-1.0.0",
                                                 "items": items}, headers={"Retry-After": "3600"})
            items.append(self.item(item))
            self.answered += 1
        return httpx.Response(200, json={"model": GEMMA, "anonymizer_version": "edge-1.0.0", "items": items})


def fake_clock(step_s=0.0):
    """perf_counter stand-in: every request takes the worker time plus 100 ms."""
    t = [0.0]

    def clock():
        t[0] += step_s
        return t[0]
    return clock


def do_run(data, worker, split="test", **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(worker))
    ids = ec.split_ids(split, data["comments"], data["splits"])
    options = {"base": BASE, "token": TOKEN, "split": split, "budget": None, "client": client,
               "comments": data["comments"], "ids": ids, "out_dir": data["out"], "sleep": lambda s: None,
               "clock": fake_clock(), "log": lambda line: None, **kwargs}
    return ec.run(**options)


def records(data, split="test"):
    return ec.read_records(ec.predictions_path(data["out"], split))


# ------------------------------------------------------------------ splits and prices


def test_split_ids(data):
    comments, splits = data["comments"], data["splits"]
    assert len(comments) == 20 and "S0005" not in comments
    assert ec.split_ids("test", comments, splits) == list(comments)[:12]
    assert ec.split_ids("dev_quick", comments, splits) == list(comments)[12:16]
    assert ec.split_ids("all", comments, splits) == list(comments)
    assert ec.split_ids("benchmark300", comments, splits) == list(comments)  # fewer than 300 here


def test_benchmark_is_the_first_300_comments_of_the_real_file():
    from enps import config
    comments = ec.read_comments(config.resolve(ec.RESPONSES))
    ids = ec.split_ids("benchmark300", comments, config.resolve(ec.SPLITS_FILE))
    assert len(ids) == 300 and ids == list(comments)[:300]


def test_neuron_prices():
    gemma = ec.PRICES[GEMMA]
    assert gemma.neurons(1_000_000, 0) == 9_091 and gemma.neurons(0, 1_000_000) == 27_273
    assert round(gemma.neurons(2100, 40), 4) == 20.1820
    assert round(ec.estimate_per_comment(gemma), 4) == 40.364


# ------------------------------------------------------------------ run


def test_run_sends_batches_of_ten_with_the_token(data):
    worker = FakeWorker()
    report = do_run(data, worker)
    assert report.sent == 12 and report.stopped is None
    assert [len(r["body"]["items"]) for r in worker.requests] == [10, 2]
    first = worker.requests[0]
    assert first["url"] == BASE + "/api/v1/internal/evaluate"
    assert first["headers"]["X-Eval-Token"] == TOKEN
    assert first["body"]["verifier"] is None
    assert first["body"]["items"][0] == {"id": "S0001", "comment": "comentário 1 sobre a escala"}
    saved = records(data)
    assert list(saved) == ec.split_ids("test", data["comments"], data["splits"])
    one = saved["S0004"]
    assert one["status"] == "done" and one["critical"] is True and one["critical_types"] == ["am"]
    assert one["variants"] == {"llm_only": True, "llm_lexicon": True, "full": True}
    assert one["model"] == GEMMA and one["anonymizer_version"] == "edge-1.0.0"
    assert one["ai_ms"] == 1995 and one["worker_ms"] == 2000
    assert one["prompt_tokens"] == 2100 and one["output_tokens"] == 40 and one["neurons"] == 20.182
    assert report.neurons == pytest.approx(12 * 20.182, abs=0.01)


def test_end_to_end_time_shares_the_request_overhead(data):
    worker = FakeWorker(work_ms=1000)
    # Each request measures 10.5 s on the client for 2 items of 1 s: 8.5 s overhead, 4.25 s each.
    do_run(data, worker, batch_size=2, clock=fake_clock(10.5))
    assert records(data)["S0001"]["e2e_ms"] == 1000 + 4250


def test_run_resumes_and_resends_failed_ids(data):
    do_run(data, FakeWorker(fail_ids={"S0002"}), limit=4)
    saved = records(data)
    assert list(saved) == ["S0001", "S0002", "S0003", "S0004"] and saved["S0002"]["status"] == "failed"
    worker = FakeWorker()
    report = do_run(data, worker)
    sent = [i["id"] for r in worker.requests for i in r["body"]["items"]]
    assert sent[0] == "S0002" and "S0001" not in sent and len(sent) == 9
    assert report.sent == 9 and records(data)["S0002"]["status"] == "done"


def test_limit_and_pause(data):
    pauses = []
    report = do_run(data, FakeWorker(), batch_size=3, limit=7, sleep=pauses.append, pause_s=0.5)
    assert report.sent == 7 and report.stopped == "limit"
    assert pauses == [0.5, 0.5]  # between the three requests, not before the first


def test_budget_stops_before_it_is_exceeded(data):
    worker = FakeWorker()
    report = do_run(data, worker, budget=100.0)
    # First batch on the estimate (two calls, 40.364 each): 2 comments; then on the real cost (20.182): 2 more.
    assert [len(r["body"]["items"]) for r in worker.requests] == [2, 2]
    assert report.stopped == "budget" and report.sent == 4
    assert report.neurons == pytest.approx(80.728) and report.neurons <= 100.0
    assert do_run(data, FakeWorker(), budget=10.0).sent == 0


def test_quota_keeps_the_items_done_and_stops(data):
    worker = FakeWorker(quota_after=3)
    report = do_run(data, worker)
    assert report.stopped == "quota" and report.sent == 3
    assert list(records(data)) == ["S0001", "S0002", "S0003"]
    assert len(worker.requests) == 1


@pytest.mark.parametrize("status, message", [(404, "EVAL_TOKEN"), (403, "EDGE_EVAL_TOKEN"), (500, "HTTP 500")])
def test_refusals_raise(data, status, message):
    with pytest.raises(ec.EvaluateError, match=message):
        do_run(data, FakeWorker(status=status))


def test_unknown_model_price_stops_the_run(data):
    with pytest.raises(ec.EvaluateError, match="no neuron price"):
        do_run(data, FakeWorker(), prices={"@cf/other/model": ec.Prices(1, 1)})
    assert len(records(data)) == 10  # the answers already paid for are kept


def test_batch_size_is_checked(data):
    with pytest.raises(ValueError):
        do_run(data, FakeWorker(), batch_size=11)


# ------------------------------------------------------------------ CLI


def cli(data, *args):
    return ["--split", "test", "--splits", str(data["splits"]), "--out-dir", str(data["out"]), *args]


def test_main_run_needs_the_token(data, capsys):
    assert ec.main(["run", *cli(data, "--responses", str(data["responses"]))], environ={}) == 2
    assert "EDGE_EVAL_TOKEN" in capsys.readouterr().err


def test_main_run_and_summary(data, capsys):
    client = httpx.Client(transport=httpx.MockTransport(FakeWorker()))
    args = ["run", *cli(data, "--responses", str(data["responses"]), "--base", BASE, "--pause", "0")]
    assert ec.main(args, client=client, environ={"EDGE_EVAL_TOKEN": TOKEN}) == 0
    assert "test: 12 comments sent" in capsys.readouterr().out
    assert ec.main(["summary", *cli(data, "--intended", str(data["intended"]),
                                    "--human", str(data["out"] / "none.csv"))]) == 0
    perf = json.loads((data["out"] / "performance_test.json").read_text(encoding="utf-8"))
    assert perf["n_comments"] == 12 and perf["status"] == "measured"
    classification = json.loads((data["out"] / "classification_test.json").read_text(encoding="utf-8"))
    assert classification["intended"]["n"] == 12 and classification["human"] is None
    client.close()


def test_main_reports_a_refusal(data, capsys):
    client = httpx.Client(transport=httpx.MockTransport(FakeWorker(status=403)))
    args = ["run", *cli(data, "--responses", str(data["responses"]), "--base", BASE)]
    assert ec.main(args, client=client, environ={"EDGE_EVAL_TOKEN": "wrong"}) == 1
    assert "refused the token" in capsys.readouterr().err
    client.close()


# ------------------------------------------------------------------ summary


def test_summary_writes_performance_and_agreement(data, tmp_path):
    do_run(data, FakeWorker(fail_ids={"S0003"}), clock=fake_clock(30.0))
    human = tmp_path / "human_test.csv"
    with open(data["intended"], encoding="utf-8") as src, open(human, "w", encoding="utf-8") as dst:
        dst.write(src.read().replace("inf,neg", "inf,neu", 1))  # one disagreement with the model
    result = ec.summarize("test", out_dir=data["out"], splits_path=data["splits"], intended=data["intended"],
                          human=human, now=NOW)
    perf = result["performance"]
    assert perf["status"] == "measured" and perf["n_comments"] == 11
    assert perf["statuses"] == {"done": 11, "failed": 1}
    assert perf["model"] == GEMMA and perf["prompt_version"] == "classify.v3+708a3315"
    assert perf["latency_ai"] == {"mean_s": 1.995, "p50_s": 1.995, "p95_s": 1.995, "max_s": 1.995}
    assert perf["latency_worker"]["p50_s"] == 2.0
    assert perf["latency"]["p50_s"] > perf["latency_worker"]["p50_s"]
    assert perf["tokens"] == {"prompt_mean": 2100, "output_mean": 40, "prompt_total": 23100, "output_total": 440}
    assert perf["neurons_per_comment_estimate"] == pytest.approx(20.182)
    assert perf["neurons_total"] == pytest.approx(11 * 20.182, abs=0.01)
    assert perf["neuron_prices"] == {GEMMA: {"input_per_m": 9091, "output_per_m": 27273}}
    assert perf["verifier_share"] == 0

    intended = result["classification"]["intended"]
    assert intended["reference"] == "intended" and intended["n"] == 11
    assert intended["missing_predictions"] == ["S0003"]
    assert intended["sentiment"]["accuracy"] == 1.0
    assert intended["critical"]["recall"] == 1.0 and intended["critical"]["fp"] == 0
    assert set(intended["critical_variants"]) == {"llm_only", "llm_lexicon", "full"}
    human_result = result["classification"]["human"]
    assert human_result["reference"] == "human" and human_result["sentiment"]["accuracy"] == pytest.approx(10 / 11)
    assert result["classification"]["panel"] is None  # no panel file given

    written = json.loads((data["out"] / "performance_test.json").read_text(encoding="utf-8"))
    assert written["generated_at"] == "2026-11-11T15:00:00+00:00"
    assert (data["out"] / "classification_test.json").exists()


def test_summary_without_predictions(data):
    result = ec.summarize("dev_quick", out_dir=data["out"], splits_path=data["splits"], intended=data["intended"],
                          human=data["out"] / "none.csv", now=NOW)
    assert result["performance"]["status"] == "not_measured"
    assert result["performance"]["latency"] == {"mean_s": None, "p50_s": None, "p95_s": None, "max_s": None}
    assert result["classification"]["intended"]["n"] == 0


def test_summary_adds_the_panel_reference_when_present(data, tmp_path):
    do_run(data, FakeWorker(), clock=fake_clock(30.0))
    panel = tmp_path / "panel_test.csv"
    with open(data["intended"], encoding="utf-8") as src, open(panel, "w", encoding="utf-8") as dst:
        dst.write(src.read().replace("inf,neg", "inf,pos", 1))
    result = ec.summarize("test", out_dir=data["out"], splits_path=data["splits"], intended=data["intended"],
                          human=data["out"] / "none.csv", panel=panel, now=NOW)
    classification = result["classification"]
    assert classification["human"] is None
    assert classification["panel"]["reference"] == "panel" and classification["panel"]["n"] == 12
    assert classification["panel"]["sentiment"]["accuracy"] == pytest.approx(11 / 12)
    assert classification["intended"]["sentiment"]["accuracy"] == 1.0
    missing = ec.summarize("test", out_dir=data["out"], splits_path=data["splits"], intended=data["intended"],
                           human=data["out"] / "none.csv", panel=tmp_path / "absent.csv", now=NOW)
    assert missing["classification"]["panel"] is None
