"""End-to-end pipeline on the synthetic data.

    python -m enps.pipeline all          # generate, anonymize, classify, evaluate, export, build the site
    python -m enps.pipeline classify     # fresh pipeline database, synthetic batch through the real queue
    python -m enps.pipeline evaluate     # metrics against the generator labels and, when present, human labels
    python -m enps.pipeline export       # dashboard JSON for the site and metrics/metrics.json

The model calls go through llm_cache/. With ENPS_LLM_MODE=offline every answer is replayed from the cache,
so the whole pipeline runs without Ollama and gives the same classifications. Timing metrics come only from
a live run (ENPS_LLM_MODE=live): an offline replay keeps the last measured benchmark.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from enps import config
from enps.storage import db

SYNTH = Path("data/synthetic")
METRICS = Path("metrics")
PREDICTIONS = METRICS / "predictions_synthetic.jsonl"
BENCHMARK = METRICS / "benchmark.json"
BENCHMARK_SIZE = 300


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, data: Any) -> None:
    path = config.resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    path = config.resolve(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pipeline_db_path() -> Path:
    return config.resolve(config.settings()["paths"]["pipeline_database"])


# ----------------------------------------------------------------------------- steps

def step_generate() -> None:
    from enps.synth.__main__ import main as synth_main
    synth_main(["--quiet"])


def step_anonymization() -> dict[str, Any]:
    from enps.evaluation.anonymization import main as anon_main
    anon_main(["--out", str(config.resolve(METRICS / "anonymization.json"))])
    return _read_json(METRICS / "anonymization.json")


def step_classify(*, fresh: bool = True) -> int:
    """Queue every synthetic answer in a pipeline database and drain the queue with the real worker."""
    from enps.queue.ingest import ingest_synthetic_csv
    from enps.queue.worker import drain

    path = pipeline_db_path()
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    conn = db.open_db(path)
    try:
        report = ingest_synthetic_csv(conn, config.resolve(SYNTH / "responses.csv"))
        print(f"queued {report.inserted} synthetic answers ({report.already_present} already present)")
    finally:
        conn.close()
    return drain(path)


def _job_rows(conn) -> list[Mapping[str, Any]]:
    return conn.execute(
        """SELECT r.source_ref, r.status, r.started_at, r.finished_at, c.*
             FROM responses r JOIN classifications c ON c.response_id = r.id
            WHERE r.dataset = 'synthetic'
         ORDER BY r.started_at, r.id"""
    ).fetchall()


def _stage_output(stages: Sequence[Mapping[str, Any]], *names: str) -> Mapping[str, Any]:
    for stage in stages:
        if stage.get("name") in names and stage.get("ok") and stage.get("output"):
            return stage["output"]
    return {}


def critical_variants(stages: Sequence[Mapping[str, Any]], final: bool) -> dict[str, bool]:
    """Critical flag under each configuration, rebuilt from the recorded stages."""
    llm = bool(_stage_output(stages, "classify", "classify_retry").get("c"))
    strong = any(h.get("strength") == "strong" for h in _stage_output(stages, "lexicon").get("hits", []))
    return {"llm_only": llm, "llm_lexicon": llm or strong, "full": final}


def step_export_predictions() -> list[dict[str, Any]]:
    conn = db.open_db(pipeline_db_path())
    try:
        rows = _job_rows(conn)
    finally:
        conn.close()
    preds = []
    for row in rows:
        stages = json.loads(row["stages"] or "[]")
        preds.append({
            "id": row["source_ref"], "status": row["status"],
            "themes": json.loads(row["themes"]), "sentiment": row["sentiment"],
            "critical": bool(row["critical"]), "critical_types": json.loads(row["critical_types"]),
            "critical_sources": json.loads(row["critical_sources"]),
            "variants": critical_variants(stages, bool(row["critical"])),
            "model": row["model"], "prompt_version": row["prompt_version"],
            "verifier_version": row["verifier_version"],
        })
    preds.sort(key=lambda p: p["id"])
    path = config.resolve(PREDICTIONS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for p in preds:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    return preds


def _ms(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return int((b - a).total_seconds() * 1000)


def step_benchmark() -> dict[str, Any] | None:
    """Timing of the first BENCHMARK_SIZE + 1 classified comments of a live run: cold start apart."""
    from enps.evaluation.performance import hardware_spec, percentile, summarize

    conn = db.open_db(pipeline_db_path())
    try:
        rows = [r for r in _job_rows(conn) if r["status"] in ("done", "review")]
    finally:
        conn.close()
    if not rows:
        return None
    stages_by_row = [json.loads(r["stages"] or "[]") for r in rows]
    if any(s.get("from_cache") for stages in stages_by_row for s in stages):
        print("benchmark skipped: answers came from the cache (run with ENPS_LLM_MODE=live)")
        return _read_json(BENCHMARK)
    fields = ("total_ms", "load_ms", "prompt_tokens", "cached_tokens", "output_tokens", "prompt_ms", "eval_ms")
    records = [{f: sum(int(s.get(f) or 0) for s in stages) for f in fields} for stages in stages_by_row]
    selected = records[: BENCHMARK_SIZE + 1]
    wall = [_ms(r["started_at"], r["finished_at"]) for r in rows[1: BENCHMARK_SIZE + 1]]
    wall = [w / 1000 for w in wall if w is not None]
    result = {
        "status": "measured", "measured_at": _now(), "n_comments": len(selected) - 1,
        "model": rows[0]["model"], "quantization": rows[0]["quantization"],
        "model_digest": rows[0]["model_digest"], "runtime_version": rows[0]["runtime_version"],
        "prompt_version": rows[0]["prompt_version"],
        "model_time": summarize(selected),
        "wall_time_per_comment": {
            "n": len(wall), "total_s": sum(wall), "mean_s": sum(wall) / len(wall) if wall else None,
            "p50_s": percentile(wall, 50), "p95_s": percentile(wall, 95),
        },
        "hardware": hardware_spec(),
    }
    _write_json(BENCHMARK, result)
    return result


def _wilson(k: int, n: int) -> list[float] | None:
    if not n:
        return None
    z = 1.959963984540054
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def step_evaluate() -> dict[str, Any]:
    from enps.evaluation.classification import (Labels, critical_metrics, evaluate, read_reference,
                                                read_split)

    preds_raw = {}
    with open(config.resolve(PREDICTIONS), encoding="utf-8") as fh:
        for line in fh:
            p = json.loads(line)
            preds_raw[p["id"]] = p
    preds = {i: Labels(frozenset(p["themes"]), p["sentiment"], p["critical"], frozenset(p["critical_types"]))
             for i, p in preds_raw.items()}
    test_ids = read_split(config.resolve(SYNTH / "splits.json"), "test")
    out: dict[str, Any] = {}

    def run(ref_path: Path, ids: Sequence[str] | None, reference: str) -> dict[str, Any]:
        ref, extra = read_reference(ref_path, ids)
        result = evaluate(ref, preds, extra)
        result["reference"] = reference
        variants = {}
        for name in ("llm_only", "llm_lexicon", "full"):
            vp = {i: Labels(lab.themes, lab.sentiment, preds_raw[i]["variants"][name], lab.critical_types)
                  for i, lab in preds.items()}
            variants[name] = critical_metrics({i: ref[i] for i in ref if i in vp}, vp, config.critical_codes())
        result["critical_variants"] = variants
        return result

    out["intended_test"] = run(config.resolve(SYNTH / "intended_labels.csv"), test_ids, "intended")
    out["intended_all"] = run(config.resolve(SYNTH / "intended_labels.csv"), None, "intended")
    human = config.resolve("data/labels/human_test.csv")
    out["human_test"] = run(human, test_ids, "human") if human.exists() else None
    panel = config.resolve("data/labels/panel_test.csv")
    out["panel_test"] = run(panel, test_ids, "panel") if panel.exists() else None
    out["panel_vs_intended"] = None
    readings = config.resolve("data/labels/panel/readings.json")
    if readings.exists():
        from enps.evaluation.panel import summary as panel_summary
        out["panel_readings"] = panel_summary(readings)
    if panel.exists():
        # How far the two references are from each other: the panel read as predictions against the generator.
        intended_ref, intended_extra = read_reference(config.resolve(SYNTH / "intended_labels.csv"), test_ids)
        panel_ref, _ = read_reference(panel, test_ids)
        out["panel_vs_intended"] = {k: v for k, v in evaluate(intended_ref, panel_ref, intended_extra).items()
                                    if k != "errors"}
    _write_json(METRICS / "classification.json", out)
    return out


# Main reference of each mode: human labels when they exist, then the blind model panel, then the
# labels the generator intended. The generator's labels are always kept apart as well.
EDGE_REFERENCES = ("human", "panel", "intended")
LOCAL_REFERENCES = ("human_test", "panel_test", "intended_test")


def preferred(classification: Mapping[str, Any], keys: Sequence[str]) -> Mapping[str, Any] | None:
    """The first evaluation present among `keys`."""
    return next((classification[k] for k in keys if classification.get(k)), None)


def _brief(result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The evaluation output without the per-comment error list (kept in metrics/classification*.json)."""
    if not result:
        return None
    return {k: v for k, v in result.items() if k != "errors"}


def _anonymization_block(anon: Mapping[str, Any] | None) -> dict[str, Any]:
    if not anon:
        return {"status": "not_measured"}
    o = anon["overall"]
    return {
        "status": "measured", "version": anon.get("anonymizer_version"),
        "n_comments": anon.get("documents"), "n_comments_with_pii": anon.get("documents_with_gold"),
        "n_spans": o.get("gold"), "caught": o.get("caught"), "recall": o.get("recall"),
        "recall_ci95": _wilson(o.get("caught", 0), o.get("gold", 0)), "precision": o.get("precision"),
        "false_positives": o.get("false_positives"), "leak_rate": o.get("leak_rate"),
        "comments_with_leak": len({x["id"] for x in anon.get("leaks", [])}),
        "by_type": anon.get("by_gold_type"), "ms_per_comment": (anon.get("runtime") or {}).get("ms_per_comment"),
    }


def _dev_rounds() -> list[dict[str, Any]]:
    rounds = []
    for name in ("model_comparison", "dev_round_v2", "dev_round_v3"):
        data = _read_json(METRICS / f"{name}.json")
        if not data:
            continue
        for model, r in data["models"].items():
            rounds.append({
                "file": f"metrics/{name}.json", "split": data["split"], "n": data["n"], "model": model,
                # runs made before the field existed: the prompt is known from the file name
                "prompt_version": r.get("prompt_version") or {"model_comparison": "classify.v1",
                                                              "dev_round_v2": "classify.v2",
                                                              "dev_round_v3": "classify.v3"}.get(name),
                "sentiment_accuracy": r["sentiment"].get("accuracy"), "sentiment_kappa": r["sentiment"].get("kappa"),
                "themes_micro_f1": r["themes"].get("micro_f1"), "themes_macro_f1": r["themes"].get("macro_f1"),
                "critical_by_variant": {k: {m: v.get(m) for m in ("recall", "precision", "tp", "fp", "fn")}
                                        for k, v in r["critical_by_variant"].items()},
                "mean_s": r["performance"]["steady"].get("mean_s"),
                "verifier_share": r.get("verifier_share"),
            })
    return rounds


def _edge_block(llm: Mapping[str, Any]) -> dict[str, Any]:
    """Live demo on Cloudflare: Worker anonymizer + Workers AI, measured through the deployed Worker."""
    wrangler = (config.resolve("wrangler.jsonc")).read_text(encoding="utf-8")
    import re
    model = re.search(r'"AI_MODEL"\s*:\s*"([^"]+)"', wrangler)
    anon = _read_json(METRICS / "anonymization_worker.json")
    classification = _read_json(METRICS / "edge" / "classification_test.json") or {}
    perf = _read_json(METRICS / "edge" / "performance_benchmark300.json") or _read_json(
        METRICS / "edge" / "performance_test.json")
    from enps.classify.prompts import load_prompt
    return {
        "pipeline": {"provider": "workers-ai", "model": model.group(1) if model else None,
                     "prompt_version": load_prompt(llm["classify_prompt"]).prompt_version,
                     "verifier_version": load_prompt(llm["verify_prompt"]).prompt_version,
                     "anonymizer_version": (anon or {}).get("anonymizer_version")},
        "anonymization": _anonymization_block(anon),
        "classification": _brief(preferred(classification, EDGE_REFERENCES)),
        "classification_intended": _brief(classification.get("intended")),
        "performance": perf or {"status": "not_measured"},
    }


def _local_block(preds: list[dict[str, Any]], llm: Mapping[str, Any]) -> dict[str, Any]:
    """Local mode: spaCy anonymizer + Ollama on the reference server."""
    anon = _read_json(METRICS / "anonymization.json")
    classification = _read_json(METRICS / "classification.json") or {}
    bench = _read_json(BENCHMARK)
    first = next((p for p in preds if p.get("model")), {})
    performance: dict[str, Any] = {"status": "not_measured"}
    if bench:
        performance = {"status": "measured", "n_comments": bench["n_comments"], "measured_at": bench["measured_at"],
                       **bench["model_time"], "wall_time_per_comment": bench["wall_time_per_comment"],
                       "hardware": bench["hardware"]}
    return {
        "pipeline": {"provider": "ollama", "model": first.get("model") or llm["model"],
                     "quantization": (bench or {}).get("quantization"),
                     "runtime_version": (bench or {}).get("runtime_version"),
                     "prompt_version": first.get("prompt_version"),
                     "verifier_version": next((p["verifier_version"] for p in preds if p.get("verifier_version")), None),
                     "anonymizer_version": (anon or {}).get("anonymizer_version")},
        "anonymization": _anonymization_block(anon),
        "classification": _brief(preferred(classification, LOCAL_REFERENCES)),
        "classification_intended": _brief(classification.get("intended_test")),
        "classification_intended_all": _brief(classification.get("intended_all")),
        "performance": performance,
        "status_counts": {s: sum(1 for p in preds if p["status"] == s) for s in ("done", "review", "skipped", "failed")},
    }


def step_export(*, build_site: bool = True) -> dict[str, Any]:
    from enps.metrics.aggregates import build_dashboard

    conn = db.open_db(pipeline_db_path())
    try:
        dashboard = build_dashboard(conn, "synthetic")
    finally:
        conn.close()
    _write_json(Path("site/public/data/dashboard-synthetic.json"), dashboard)

    preds = [json.loads(line) for line in open(config.resolve(PREDICTIONS), encoding="utf-8")]
    llm = config.settings()["llm"]
    metrics = {
        "generated_at": _now(),
        "live_mode": "edge",
        "modes": {"edge": _edge_block(llm), "local": _local_block(preds, llm)},
        "development": _dev_rounds(),
    }
    _write_json(METRICS / "metrics.json", metrics)
    if build_site:
        from enps.export.site import main as site_main
        site_main([])
    return metrics


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m enps.pipeline", description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["all", "generate", "anonymization", "classify", "benchmark",
                                         "evaluate", "export"])
    parser.add_argument("--keep-db", action="store_true", help="classify: reuse the pipeline database")
    parser.add_argument("--no-site", action="store_true", help="export: skip the site build")
    args = parser.parse_args(argv)
    steps = {
        "generate": [step_generate],
        "anonymization": [step_anonymization],
        "classify": [lambda: step_classify(fresh=not args.keep_db), step_export_predictions, step_benchmark],
        "benchmark": [step_benchmark],
        "evaluate": [step_evaluate],
        "export": [lambda: step_export(build_site=not args.no_site)],
    }
    order = ["generate", "anonymization", "classify", "evaluate", "export"] if args.step == "all" else [args.step]
    for name in order:
        print(f"== {name}", flush=True)
        for fn in steps[name]:
            fn()
    return 0


if __name__ == "__main__":
    sys.exit(main())
