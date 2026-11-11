"""Anonymizer metrics against the planted personal data of the synthetic set.

    python -m enps.evaluation.anonymization --responses data/synthetic/responses.csv \\
        --gold data/synthetic/pii_gold.jsonl --out metrics/anonymization.json

A gold span is "caught" when every letter and digit in it is covered by the union of predicted
spans of any type ("caught_same_type": predicted spans of the gold type only). Coverage ignores
spaces and punctuation, so "Rua X, 12" counts as caught by two spans "Rua X" and "12".
A predicted span is a false positive when it overlaps no gold span. The leak rate is the share
of gold strings still present verbatim (as whole words) in the anonymized text.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from enps.anonymize import AnonymizationResult, PiiSpan

Gold = Mapping[str, Sequence[Mapping[str, Any]]]


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _covered(spans: Sequence[PiiSpan], pii_type: str | None = None) -> set[int]:
    return {
        i for s in spans if pii_type is None or s.type == pii_type for i in range(s.start, s.end)
    }


def _is_caught(gold: Mapping[str, Any], covered: set[int]) -> bool:
    start, text = gold["start"], gold["text"]
    return all(start + i in covered for i, ch in enumerate(text) if ch.isalnum())


def _leaked(gold_text: str, output: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(gold_text) + r"(?!\w)"
    return re.search(pattern, output) is not None


def _overlaps_gold(span: PiiSpan, gold_spans: Sequence[Mapping[str, Any]]) -> bool:
    return any(span.start < g["end"] and g["start"] < span.end for g in gold_spans)


def evaluate(
    gold: Gold,
    results: Mapping[str, AnonymizationResult],
    texts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Recall per gold type, precision per predicted type, leak rate and error lists.

    gold: id -> gold spans ({"start", "end", "type", "text"}); ids without planted data may be absent.
    results: id -> anonymizer output for every evaluated comment. A gold id missing here counts
    as fully missed and leaked. texts: optional id -> original comment, used to show the text of
    false positives.
    """
    gold_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    pred_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    misses, mismatches, leaks, false_positives = [], [], [], []

    for doc_id, gold_spans in gold.items():
        result = results.get(doc_id)
        spans = result.spans if result else ()
        output = result.text if result else None
        covered_any = _covered(spans)
        for g in gold_spans:
            stats = gold_stats[g["type"]]
            stats["gold"] += 1
            caught = _is_caught(g, covered_any)
            caught_typed = caught and _is_caught(g, _covered(spans, g["type"]))
            stats["caught"] += caught
            stats["caught_same_type"] += caught_typed
            row = {"id": doc_id, "type": g["type"], "text": g["text"]}
            if not caught:
                misses.append(row)
            elif not caught_typed:
                predicted = sorted({s.type for s in spans if s.start < g["end"] and g["start"] < s.end})
                mismatches.append({**row, "predicted": predicted})
            if output is None or _leaked(g["text"], output):
                stats["leaked"] += 1
                leaks.append(row)

    for doc_id, result in results.items():
        gold_spans = gold.get(doc_id, ())
        for span in result.spans:
            stats = pred_stats[span.type]
            stats["predicted"] += 1
            if not _overlaps_gold(span, gold_spans):
                stats["false_positives"] += 1
                text = texts[doc_id][span.start:span.end] if texts and doc_id in texts else None
                false_positives.append(
                    {"id": doc_id, "type": span.type, "text": text, "source": span.source})

    by_gold = {t: _gold_row(s) for t, s in sorted(gold_stats.items())}
    by_pred = {t: _pred_row(s) for t, s in sorted(pred_stats.items())}
    overall = {**_gold_row(_sum(gold_stats.values())), **_pred_row(_sum(pred_stats.values()))}
    return {
        "documents": len(results),
        "documents_with_gold": sum(1 for spans in gold.values() if spans),
        "overall": overall,
        "by_gold_type": by_gold,
        "by_predicted_type": by_pred,
        "misses": misses,
        "type_mismatches": mismatches,
        "leaks": leaks,
        "false_positives": false_positives,
    }


def _sum(rows) -> dict[str, int]:
    total: dict[str, int] = defaultdict(int)
    for row in rows:
        for key, value in row.items():
            total[key] += value
    return total


def _gold_row(s: Mapping[str, int]) -> dict[str, Any]:
    gold = s.get("gold", 0)
    return {
        "gold": gold,
        "caught": s.get("caught", 0),
        "caught_same_type": s.get("caught_same_type", 0),
        "recall": _ratio(s.get("caught", 0), gold),
        "recall_typed": _ratio(s.get("caught_same_type", 0), gold),
        "leaked": s.get("leaked", 0),
        "leak_rate": _ratio(s.get("leaked", 0), gold),
    }


def _pred_row(s: Mapping[str, int]) -> dict[str, Any]:
    predicted, fp = s.get("predicted", 0), s.get("false_positives", 0)
    return {
        "predicted": predicted,
        "false_positives": fp,
        "precision": _ratio(predicted - fp, predicted),
    }


# ------------------------------------------------------------------ files and CLI

def load_responses(path: Path) -> dict[str, str]:
    """id -> comment, exactly as stored (gold offsets point into these strings)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {row["id"]: row.get("comment") or "" for row in csv.DictReader(fh)}


def load_gold(path: Path) -> dict[str, list[dict[str, Any]]]:
    gold: dict[str, list[dict[str, Any]]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                record = json.loads(line)
                gold.setdefault(record["id"], []).extend(record.get("spans", []))
    return gold


def check_gold(gold: Gold, texts: Mapping[str, str]) -> list[str]:
    """Gold spans whose text does not match the comment at their offsets."""
    problems = []
    for doc_id, spans in gold.items():
        comment = texts.get(doc_id)
        for g in spans:
            if comment is None or comment[g["start"]:g["end"]] != g["text"]:
                problems.append(f"{doc_id} {g['type']} {g['text']!r}")
    return problems


def format_table(metrics: Mapping[str, Any]) -> str:
    lines = [f"{'type':<11}{'gold':>6}{'caught':>8}{'recall':>8}{'typed':>8}{'leak':>7}"
             f"{'pred':>7}{'FP':>5}{'prec':>7}"]
    types = sorted(set(metrics["by_gold_type"]) | set(metrics["by_predicted_type"]))

    def fmt(value: Any) -> str:
        return "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)

    for name, g, p in [(t, metrics["by_gold_type"].get(t, {}), metrics["by_predicted_type"].get(t, {}))
                       for t in types] + [("ALL", metrics["overall"], metrics["overall"])]:
        lines.append(
            f"{name:<11}{fmt(g.get('gold', 0)):>6}{fmt(g.get('caught', 0)):>8}"
            f"{fmt(g.get('recall')):>8}{fmt(g.get('recall_typed')):>8}{fmt(g.get('leak_rate')):>7}"
            f"{fmt(p.get('predicted', 0)):>7}{fmt(p.get('false_positives', 0)):>5}"
            f"{fmt(p.get('precision')):>7}"
        )
    return "\n".join(lines)


def run(responses_path: Path, gold_path: Path) -> dict[str, Any]:
    from enps.anonymize import ner
    from enps.anonymize.pipeline import ANONYMIZER_VERSION, anonymize

    texts = {k: v for k, v in load_responses(responses_path).items() if v.strip()}
    gold = load_gold(gold_path)
    problems = check_gold(gold, texts)
    load_start = time.perf_counter()
    ner.load_model()
    load_s = time.perf_counter() - load_start
    start = time.perf_counter()
    results = {doc_id: anonymize(text) for doc_id, text in texts.items()}
    elapsed = time.perf_counter() - start
    metrics = evaluate(gold, results, texts)
    metrics["anonymizer_version"] = ANONYMIZER_VERSION
    metrics["gold_offset_problems"] = problems
    metrics["runtime"] = {
        "model_load_s": round(load_s, 2),
        "anonymize_s": round(elapsed, 2),
        "ms_per_comment": round(1000 * elapsed / len(texts), 2) if texts else None,
    }
    return metrics


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate the anonymizer on the synthetic set.")
    parser.add_argument("--responses", type=Path, default=Path("data/synthetic/responses.csv"))
    parser.add_argument("--gold", type=Path, default=Path("data/synthetic/pii_gold.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("metrics/anonymization.json"))
    args = parser.parse_args(argv)
    metrics = run(args.responses, args.gold)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(format_table(metrics))
    print(f"documents {metrics['documents']}, with planted data {metrics['documents_with_gold']}, "
          f"{metrics['runtime']['ms_per_comment']} ms per comment -> {args.out}")
    if metrics["gold_offset_problems"]:
        print(f"warning: {len(metrics['gold_offset_problems'])} gold spans do not match the comments")


if __name__ == "__main__":
    main()
