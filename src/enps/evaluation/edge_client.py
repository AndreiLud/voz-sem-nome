"""Evaluation of the live demo's classifier through the deployed Cloudflare Worker.

    EDGE_EVAL_TOKEN=... python -m enps.evaluation.edge_client run --split test --budget-neurons 4000
    python -m enps.evaluation.edge_client summary --split test

`run` sends the synthetic comments of a split to POST <base>/api/v1/internal/evaluate in batches
of up to 10. The Worker runs the same path as a live submission (anonymizer, Workers AI
classification) and stores nothing. Each answer is appended to
metrics/edge/predictions_<split>.jsonl, so an interrupted run resumes where it stopped: ids
already done, review or skipped are not sent again (failed ones are).

The run stops before the next batch would go past --budget-neurons. Neurons are computed from the
token counts the Worker reports and the model's published prices (Workers AI bills neurons per
million input and output tokens; the free allocation is 10,000 neurons a day, shared by the whole
account). The next batch is estimated with the most expensive comment seen in this run, or with
two model calls per comment before the first answer.

`summary` writes metrics/edge/performance_<split>.json (latency of the model calls, of the
Worker's work and end to end per comment, tokens and neurons) and
metrics/edge/classification_<split>.json (agreement with the generator's intended labels and,
when data/labels/human_test.csv exists, with the human labels).

Timing per comment: ai = model calls (sum of the stage times), worker = the Worker's whole
work on the comment, end_to_end = worker plus an equal share of the request's network and
queuing overhead (request time minus the worker times of the batch, split over its items).

Splits: test, dev, dev_quick (from splits.json), all (every comment), benchmark300 (the first 300
comments of responses.csv, as the local benchmark times the first 300 of a run).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from enps import config
from enps.evaluation.classification import (
    Labels, critical_metrics, evaluate, read_reference, read_split, to_labels,
)
from enps.evaluation.performance import percentile
from enps.pipeline import critical_variants

EVALUATE_PATH = "/api/v1/internal/evaluate"
TOKEN_ENV = "EDGE_EVAL_TOKEN"
MAX_BATCH = 10
BENCHMARK_SIZE = 300
SPLITS = ("test", "dev", "dev_quick", "all", "benchmark300")
FINISHED = ("done", "review", "skipped")
CLASSIFIED = ("done", "review")
REQUEST_TIMEOUT_S = 180.0
RESPONSES = Path("data/synthetic/responses.csv")
SPLITS_FILE = Path("data/synthetic/splits.json")
INTENDED = Path("data/synthetic/intended_labels.csv")
HUMAN = Path("data/labels/human_test.csv")
OUT_DIR = Path("metrics/edge")
# Tokens of one model call used to estimate a comment before any answer (research notes:
# about 2,100 prompt tokens and 40 output tokens per call, two calls when the verifier runs).
ESTIMATE_CALL_TOKENS = (2100, 40)
ESTIMATE_CALLS = 2


@dataclass(frozen=True)
class Prices:
    """Workers AI neurons per million input and output tokens."""
    input_per_m: float
    output_per_m: float

    def neurons(self, prompt_tokens: int, output_tokens: int) -> float:
        return (prompt_tokens * self.input_per_m + output_tokens * self.output_per_m) / 1_000_000


# Published Workers AI prices of the models the Worker can use.
PRICES: dict[str, Prices] = {
    "@cf/google/gemma-4-26b-a4b-it": Prices(9_091, 27_273),
    "@cf/zai-org/glm-4.7-flash": Prices(5_500, 36_400),
}
DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it"


class EvaluateError(Exception):
    """The Worker refused the request or answered something unexpected."""


@dataclass
class RunReport:
    sent: int = 0
    neurons: float = 0.0
    stopped: str | None = None  # None (all done), "budget", "quota", "capacity" or "limit"


# ------------------------------------------------------------------ inputs


def read_comments(path: Path) -> dict[str, str]:
    """id -> comment of every row with a comment, in file order."""
    with open(path, encoding="utf-8", newline="") as fh:
        return {row["id"]: row["comment"] for row in csv.DictReader(fh) if row["comment"].strip()}


def split_ids(split: str, comments: Mapping[str, str], splits_path: Path) -> list[str]:
    if split == "all":
        return list(comments)
    if split == "benchmark300":
        return list(comments)[:BENCHMARK_SIZE]
    return [i for i in read_split(splits_path, split) if i in comments]


def predictions_path(out_dir: Path, split: str) -> Path:
    return out_dir / f"predictions_{split}.jsonl"


def read_records(path: Path) -> dict[str, dict[str, Any]]:
    """Latest record per id (a retried id is appended again)."""
    records: dict[str, dict[str, Any]] = {}
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    record = json.loads(line)
                    records[str(record["id"])] = record
    return records


# ------------------------------------------------------------------ records


def to_record(item: Mapping[str, Any], *, model: str, anonymizer_version: str | None, overhead_ms: float,
              prices: Prices | None) -> dict[str, Any]:
    """One prediction line from one item of the Worker's answer."""
    result = item.get("result") or {}
    stages = result.get("stages") or []
    status = result.get("status", "failed")
    prompt_tokens = int(result.get("prompt_tokens") or 0)
    output_tokens = int(result.get("output_tokens") or 0)
    worker_ms = float(item.get("total_ms") or 0)
    return {
        "id": str(item["id"]),
        "status": status,
        "themes": result.get("themes") or [],
        "sentiment": result.get("sentiment"),
        "critical": bool(result.get("critical")),
        "critical_types": result.get("critical_types") or [],
        "critical_sources": result.get("critical_sources") or [],
        "evidence": result.get("evidence"),
        "variants": critical_variants(stages, bool(result.get("critical"))) if status in CLASSIFIED else None,
        "error_code": result.get("error_code"),
        "model": result.get("model") or model,
        "prompt_version": result.get("prompt_version"),
        "verifier_version": result.get("verifier_version"),
        "anonymizer_version": anonymizer_version,
        "comment_anon": item.get("comment_anon"),
        "pii_counts": item.get("pii_counts") or {},
        "ai_ms": int(result.get("ai_ms") or 0),
        "worker_ms": worker_ms,
        "e2e_ms": round(worker_ms + overhead_ms, 1),
        "anonymize_ms": item.get("anonymize_ms"),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "neurons": round(prices.neurons(prompt_tokens, output_tokens), 4) if prices else None,
        "stages": stages,
    }


def batch_records(answer: Mapping[str, Any], request_ms: float,
                  prices_for: Callable[[str], Prices | None]) -> list[dict[str, Any]]:
    items = answer.get("items") or []
    if not items:
        return []
    overhead = max(0.0, request_ms - sum(float(i.get("total_ms") or 0) for i in items)) / len(items)
    out = []
    for item in items:
        model = (item.get("result") or {}).get("model") or answer.get("model") or ""
        out.append(to_record(item, model=model, anonymizer_version=answer.get("anonymizer_version"),
                             overhead_ms=overhead, prices=prices_for(model) if model else None))
    return out


# ------------------------------------------------------------------ run


def post_batch(client: httpx.Client, base: str, token: str, items: Sequence[Mapping[str, str]],
               verifier: bool | None) -> tuple[int, dict[str, Any]]:
    """(status, JSON body) of one evaluate call; raises EvaluateError on refusals."""
    response = client.post(
        base.rstrip("/") + EVALUATE_PATH,
        json={"items": list(items), "verifier": verifier},
        headers={"X-Eval-Token": token},
        timeout=REQUEST_TIMEOUT_S,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 404:
        raise EvaluateError("the evaluate route is off: set the EVAL_TOKEN secret on the Worker")
    if response.status_code == 403:
        raise EvaluateError(f"the Worker refused the token in {TOKEN_ENV}")
    if response.status_code == 503 and body.get("error") in ("quota", "capacity"):
        return response.status_code, body
    if response.status_code != 200:
        raise EvaluateError(f"HTTP {response.status_code}: {body.get('error') or 'unexpected answer'}")
    return response.status_code, body


def estimate_per_comment(prices: Prices) -> float:
    prompt, output = ESTIMATE_CALL_TOKENS
    return ESTIMATE_CALLS * prices.neurons(prompt, output)


def run(
    *,
    base: str,
    token: str,
    split: str,
    budget: float | None,
    client: httpx.Client,
    comments: Mapping[str, str],
    ids: Sequence[str],
    out_dir: Path,
    batch_size: int = MAX_BATCH,
    pause_s: float = 1.0,
    verifier: bool | None = None,
    limit: int | None = None,
    prices: Mapping[str, Prices] = PRICES,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
    log: Callable[[str], None] = print,
) -> RunReport:
    """Send the pending ids of a split in batches, appending each answer to the predictions file."""
    if not 1 <= batch_size <= MAX_BATCH:
        raise ValueError(f"batch size must be 1 to {MAX_BATCH}")
    path = predictions_path(out_dir, split)
    path.parent.mkdir(parents=True, exist_ok=True)
    done = {i for i, r in read_records(path).items() if r.get("status") in FINISHED}
    remaining = [i for i in ids if i not in done]
    pending = remaining if limit is None else remaining[:limit]
    report = RunReport()
    unknown: set[str] = set()

    def prices_for(model: str) -> Prices | None:
        if model not in prices:
            unknown.add(model)
        return prices.get(model)

    default_estimate = estimate_per_comment(prices.get(DEFAULT_MODEL) or next(iter(prices.values())))
    costliest = 0.0  # most expensive comment of this run so far
    log(f"{split}: {len(ids) - len(remaining)} done before, {len(pending)} to send")
    position = 0
    while position < len(pending):
        size = min(batch_size, len(pending) - position)
        if budget is not None:
            size = min(size, math.floor((budget - report.neurons) / (costliest or default_estimate)))
            if size < 1:
                report.stopped = "budget"
                break
        if position:
            sleep(pause_s)
        batch = pending[position:position + size]
        started = clock()
        status, answer = post_batch(client, base, token, [{"id": i, "comment": comments[i]} for i in batch], verifier)
        records = batch_records(answer, (clock() - started) * 1000, prices_for)
        _append(path, records)
        spent = [r["neurons"] for r in records if r["neurons"] is not None]
        report.sent += len(records)
        report.neurons += sum(spent)
        costliest = max([costliest, *spent])
        position += len(batch)
        log(f"[{len(ids) - len(remaining) + position}/{len(ids)}] {len(records)} answers, "
            f"{report.neurons:.1f} neurons this run")
        if unknown:
            raise EvaluateError(f"no neuron price for {sorted(unknown)}: add it to PRICES")
        if status == 503:
            report.stopped = answer["error"]
            break
    if report.stopped is None and len(pending) < len(remaining):
        report.stopped = "limit"
    return report


def _append(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ summary


def latency_stats(values_ms: Sequence[float]) -> dict[str, float | None]:
    seconds = [v / 1000 for v in values_ms]
    return {
        "mean_s": sum(seconds) / len(seconds) if seconds else None,
        "p50_s": percentile(seconds, 50),
        "p95_s": percentile(seconds, 95),
        "max_s": max(seconds) if seconds else None,
    }


def _first(records: Sequence[Mapping[str, Any]], key: str) -> Any:
    return next((r[key] for r in records if r.get(key)), None)


def performance(records: Sequence[Mapping[str, Any]], split: str, now: datetime) -> dict[str, Any]:
    """Latency, tokens and neurons of the classified comments of a predictions file."""
    classified = [r for r in records if r.get("status") in CLASSIFIED]
    n = len(classified)
    prompt = sum(r["prompt_tokens"] for r in classified)
    output = sum(r["output_tokens"] for r in classified)
    neurons = [r["neurons"] for r in classified if r.get("neurons") is not None]
    model = _first(classified, "model")
    return {
        "status": "measured" if n else "not_measured",
        "generated_at": now.replace(microsecond=0).isoformat(),
        "split": split,
        "provider": "workers-ai",
        "model": model,
        "prompt_version": _first(classified, "prompt_version"),
        "verifier_version": _first(classified, "verifier_version"),
        "anonymizer_version": _first(classified, "anonymizer_version"),
        "n_comments": n,
        "statuses": dict(sorted(Counter(r.get("status") for r in records).items())),
        "verifier_share": sum(1 for r in classified if r.get("verifier_version")) / n if n else None,
        "latency": latency_stats([r["e2e_ms"] for r in classified]),
        "latency_worker": latency_stats([r["worker_ms"] for r in classified]),
        "latency_ai": latency_stats([r["ai_ms"] for r in classified]),
        "tokens": {
            "prompt_mean": prompt / n if n else None,
            "output_mean": output / n if n else None,
            "prompt_total": prompt,
            "output_total": output,
        },
        "neurons_total": round(sum(neurons), 2),
        "neurons_per_comment_estimate": sum(neurons) / len(neurons) if neurons else None,
        "neuron_prices": {m: {"input_per_m": p.input_per_m, "output_per_m": p.output_per_m}
                          for m, p in PRICES.items() if m == model},
    }


def agreement(records: Sequence[Mapping[str, Any]], ref_path: Path, ids: Sequence[str] | None,
              reference: str) -> dict[str, Any]:
    """enps.evaluation.classification metrics, plus the critical recall of each configuration."""
    classified = [r for r in records if r.get("status") in CLASSIFIED]
    pred = {str(r["id"]): to_labels(r) for r in classified}
    ref, extra = read_reference(ref_path, ids)
    result = evaluate(ref, pred, extra)
    result["reference"] = reference
    common = {i: ref[i] for i in ref if i in pred}
    variants = {}
    for name in ("llm_only", "llm_lexicon", "full"):
        flagged = {str(r["id"]): Labels(frozenset(r["themes"]), r["sentiment"], bool((r.get("variants") or {}).get(name)),
                                        frozenset(r["critical_types"])) for r in classified}
        variants[name] = critical_metrics(common, flagged, config.critical_codes())
    result["critical_variants"] = variants
    return result


def summarize(split: str, *, out_dir: Path, splits_path: Path, intended: Path, human: Path,
              now: datetime | None = None) -> dict[str, Any]:
    """Write performance_<split>.json and classification_<split>.json; return both."""
    now = now or datetime.now(timezone.utc)
    records = list(read_records(predictions_path(out_dir, split)).values())
    ids = None if split in ("all", "benchmark300") else read_split(splits_path, split)
    if split == "benchmark300":
        ids = [str(r["id"]) for r in records]
    perf = performance(records, split, now)
    classification = {
        "split": split,
        "intended": agreement(records, intended, ids, "intended"),
        "human": agreement(records, human, ids, "human") if human.exists() else None,
    }
    for name, data in (("performance", perf), ("classification", classification)):
        target = out_dir / f"{name}_{split}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"performance": perf, "classification": classification}


# ------------------------------------------------------------------ CLI


def _verifier(value: str) -> bool | None:
    return {"on": True, "off": False, "default": None}[value]


def main(argv: Sequence[str] | None = None, *, client: httpx.Client | None = None,
         environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the live demo's classifier through the Worker.")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="send the comments of a split to the Worker")
    run_cmd.add_argument("--base", default=config.settings()["site"]["base_url"], help="site URL of the Worker")
    run_cmd.add_argument("--budget-neurons", type=float, default=None,
                         help="stop before this run would spend more neurons")
    run_cmd.add_argument("--batch-size", type=int, default=MAX_BATCH, help=f"comments per request (1 to {MAX_BATCH})")
    run_cmd.add_argument("--pause", type=float, default=1.0, help="seconds between requests")
    run_cmd.add_argument("--verifier", choices=("default", "on", "off"), default="default")
    run_cmd.add_argument("--limit", type=int, default=None, help="send at most this many comments")
    run_cmd.add_argument("--responses", type=Path, default=RESPONSES)
    sum_cmd = commands.add_parser("summary", help="write performance and agreement files of a split")
    sum_cmd.add_argument("--intended", type=Path, default=INTENDED)
    sum_cmd.add_argument("--human", type=Path, default=HUMAN)
    for sub in (run_cmd, sum_cmd):
        sub.add_argument("--split", choices=SPLITS, required=True)
        sub.add_argument("--splits", type=Path, default=SPLITS_FILE)
        sub.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    out_dir = config.resolve(args.out_dir)
    splits_path = config.resolve(args.splits)

    if args.command == "summary":
        result = summarize(args.split, out_dir=out_dir, splits_path=splits_path,
                           intended=config.resolve(args.intended), human=config.resolve(args.human))
        perf, intended = result["performance"], result["classification"]["intended"]
        print(f"{args.split}: n={perf['n_comments']} p50={perf['latency']['p50_s']} s "
              f"p95={perf['latency']['p95_s']} s neurons/comment={perf['neurons_per_comment_estimate']} "
              f"sentiment_acc={intended['sentiment']['accuracy']} critical_recall={intended['critical']['recall']}")
        return 0

    token = (os.environ if environ is None else environ).get(TOKEN_ENV, "")
    if not token:
        print(f"set {TOKEN_ENV} to the Worker's EVAL_TOKEN secret", file=sys.stderr)
        return 2
    comments = read_comments(config.resolve(args.responses))
    ids = split_ids(args.split, comments, splits_path)
    own_client = client is None
    http = client or httpx.Client()
    try:
        report = run(base=args.base, token=token, split=args.split, budget=args.budget_neurons, client=http,
                     comments=comments, ids=ids, out_dir=out_dir, batch_size=args.batch_size,
                     pause_s=args.pause, verifier=_verifier(args.verifier), limit=args.limit)
    except (EvaluateError, httpx.HTTPError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if own_client:
            http.close()
    reason = f", stopped by {report.stopped}" if report.stopped else ""
    print(f"{args.split}: {report.sent} comments sent, {report.neurons:.1f} neurons{reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
