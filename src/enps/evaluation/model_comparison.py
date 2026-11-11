"""Compare candidate models on the dev_quick split before choosing one.

For each model: anonymize every dev_quick comment, run the full classification (lexicon, stage 1 and the
verifier when its gate opens), time it, and score it against the generator's intended labels. This reference is
weaker than human labels; it is used only to choose a model and to tune on dev data, never on the test split.

    python -m enps.evaluation.model_comparison --models qwen3.5:2b-q4_K_M gemma4:e2b-it-qat
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from enps import config
from enps.classify.pipeline import classify_comment
from enps.classify.types import ClassificationResult
from enps.evaluation.classification import Labels, critical_metrics, evaluate, read_reference, read_split
from enps.evaluation.performance import hardware_spec, record_from_result, summarize
from enps.llm.ollama import OllamaClient


def _stage_output(result: ClassificationResult, *names: str) -> dict[str, Any] | None:
    for stage in result.stages:
        if stage.name in names and stage.ok and stage.output:
            return stage.output
    return None


def _strong_lexicon(result: ClassificationResult) -> bool:
    lex = _stage_output(result, "lexicon") or {}
    return any(hit.get("strength") == "strong" for hit in lex.get("hits", []))


def variants(result: ClassificationResult) -> dict[str, bool]:
    """Critical flag under each configuration, rebuilt from the recorded stages."""
    stage1 = _stage_output(result, "classify", "classify_retry") or {}
    llm = bool(stage1.get("c"))
    with_lexicon = llm or _strong_lexicon(result)
    return {"llm_only": llm, "llm_lexicon": with_lexicon, "full": result.critical}


def to_labels(result: ClassificationResult) -> Labels:
    return Labels(themes=frozenset(result.themes), sentiment=result.sentiment,
                  critical=result.critical, critical_types=frozenset(result.critical_types))


def run_model(model: str, texts: dict[str, str], *, mode: str) -> dict[str, Any]:
    client = OllamaClient(model=model, mode=mode)
    items: list[dict[str, Any]] = []
    preds: dict[str, ClassificationResult] = {}
    for i, (sid, text) in enumerate(texts.items(), start=1):
        t0 = time.perf_counter()
        result = classify_comment(text, client)
        wall_ms = int((time.perf_counter() - t0) * 1000)
        preds[sid] = result
        items.append({"id": sid, "wall_ms": wall_ms, "status": result.status,
                      "stages": [s.name for s in result.stages if s.name != "lexicon"],
                      "variants": variants(result), **record_from_result(result)})
        print(f"  {model} {i:>2}/{len(texts)} {sid} {wall_ms / 1000:6.1f}s {result.status:6} "
              f"t={','.join(result.themes)} s={result.sentiment} c={int(result.critical)}"
              f"{':' + ','.join(result.critical_types) if result.critical else ''}", flush=True)
    return {"client": client, "items": items, "preds": preds}


def score(run: dict[str, Any], ref: dict[str, Labels], extra: dict[str, dict[str, str]]) -> dict[str, Any]:
    preds = {sid: to_labels(r) for sid, r in run["preds"].items()}
    quality = evaluate(ref, preds, extra)
    by_variant = {}
    for name in ("llm_only", "llm_lexicon", "full"):
        flags = {item["id"]: item["variants"][name] for item in run["items"]}
        variant_preds = {sid: replace(lab, critical=flags[sid]) for sid, lab in preds.items()}
        by_variant[name] = critical_metrics(ref, variant_preds, config.critical_codes())
    return {
        "model_info": run["client"].model_info(),
        "prompt_version": next((r.prompt_version for r in run["preds"].values() if r.prompt_version), None),
        "verifier_version": next((r.verifier_version for r in run["preds"].values() if r.verifier_version), None),
        "performance": summarize(run["items"]),
        "sentiment": quality["sentiment"],
        "themes": {k: quality["themes"][k] for k in ("micro_f1", "macro_f1") if k in quality["themes"]},
        "themes_detail": quality["themes"],
        "critical": quality["critical"],
        "critical_by_variant": by_variant,
        "status_counts": {s: sum(1 for i in run["items"] if i["status"] == s) for s in ("done", "review", "skipped")},
        "verifier_share": sum(1 for i in run["items"] if any(n.startswith("verify") for n in i["stages"])) / max(1, len(run["items"])),
        "errors": quality["errors"],
        "items": run["items"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--split", default="dev_quick")
    parser.add_argument("--mode", default="cache_first", choices=["live", "cache_first", "offline"])
    parser.add_argument("--out", default="metrics/model_comparison.json")
    args = parser.parse_args()

    from enps.anonymize import anonymize

    synth = config.resolve("data/synthetic")
    ids = read_split(synth / "splits.json", args.split)
    with open(synth / "responses.csv", encoding="utf-8", newline="") as fh:
        comments = {row["id"]: row["comment"] for row in csv.DictReader(fh)}
    texts = {sid: anonymize(comments[sid]).text for sid in ids}
    ref, extra = read_reference(synth / "intended_labels.csv", ids)

    report: dict[str, Any] = {"split": args.split, "n": len(ids), "reference": "generator intended labels",
                              "hardware": hardware_spec(), "models": {}}
    for model in args.models:
        print(f"== {model}", flush=True)
        report["models"][model] = score(run_model(model, texts, mode=args.mode), ref, extra)
        out = config.resolve(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
