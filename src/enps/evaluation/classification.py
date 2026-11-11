"""Classification metrics against the reference labels.

    python -m enps.evaluation.classification --pred preds.jsonl \\
        --ref data/synthetic/intended_labels.csv --ids test --out metrics/classification_test.json

Predictions: JSONL with id, themes, sentiment, critical, critical_types (lists or "|" joined).
Reference: CSV with the columns of intended_labels.csv. Rows without themes (no comment) are skipped.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from enps import config

Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Labels:
    themes: frozenset[str]
    sentiment: str | None
    critical: bool
    critical_types: frozenset[str]


# ------------------------------------------------------------------ statistics

def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float | None, float | None]:
    """Wilson score interval for a proportion; (None, None) when n is 0."""
    if n == 0:
        return None, None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    if precision is None or recall is None or precision + recall == 0:
        f1 = 0.0 if (tp + fp + fn) else None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def confusion_matrix(ref: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> list[list[int]]:
    """Rows are reference labels, columns are predicted labels."""
    pos = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for r, p in zip(ref, pred):
        matrix[pos[r]][pos[p]] += 1
    return matrix


def cohen_kappa(matrix: Sequence[Sequence[int]]) -> float | None:
    n = sum(map(sum, matrix))
    if n == 0:
        return None
    observed = sum(matrix[i][i] for i in range(len(matrix))) / n
    rows = [sum(row) for row in matrix]
    cols = [sum(col) for col in zip(*matrix)]
    expected = sum(r * c for r, c in zip(rows, cols)) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


# --------------------------------------------------------------------- metrics

def sentiment_metrics(ref: Mapping[str, Labels], pred: Mapping[str, Labels],
                      labels: Sequence[str]) -> dict[str, Any]:
    ids = [i for i in ref if ref[i].sentiment in labels]
    pairs = [(ref[i].sentiment, pred[i].sentiment) for i in ids]
    labeled = [(r, p) for r, p in pairs if p in labels]
    matrix = confusion_matrix([r for r, _ in labeled], [p for _, p in labeled], labels)
    correct = sum(r == p for r, p in pairs)
    return {
        "n": len(pairs),
        "accuracy": _ratio(correct, len(pairs)),
        "kappa": cohen_kappa(matrix),
        "labels": list(labels),
        "confusion": matrix,
        "missing_prediction": len(pairs) - len(labeled),
    }


def theme_metrics(ref: Mapping[str, Labels], pred: Mapping[str, Labels],
                  codes: Sequence[str]) -> dict[str, Any]:
    per_theme: dict[str, Any] = {}
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for code in codes:
        tp = sum(code in ref[i].themes and code in pred[i].themes for i in ref)
        fp = sum(code not in ref[i].themes and code in pred[i].themes for i in ref)
        fn = sum(code in ref[i].themes and code not in pred[i].themes for i in ref)
        support = tp + fn
        per_theme[code] = {**prf(tp, fp, fn), "support": support, "tp": tp, "fp": fp, "fn": fn}
        for key, value in (("tp", tp), ("fp", fp), ("fn", fn)):
            totals[key] += value
    with_support = [m["f1"] for m in per_theme.values() if m["support"] > 0]
    return {
        "per_theme": per_theme,
        "macro_f1": sum(with_support) / len(with_support) if with_support else None,
        "micro_f1": prf(totals["tp"], totals["fp"], totals["fn"])["f1"],
        "exact_match": _ratio(sum(ref[i].themes == pred[i].themes for i in ref), len(ref)),
    }


def critical_metrics(ref: Mapping[str, Labels], pred: Mapping[str, Labels],
                     codes: Sequence[str]) -> dict[str, Any]:
    tp = sum(ref[i].critical and pred[i].critical for i in ref)
    fp = sum(not ref[i].critical and pred[i].critical for i in ref)
    fn = sum(ref[i].critical and not pred[i].critical for i in ref)
    tn = len(ref) - tp - fp - fn
    by_type: dict[str, Any] = {}
    for code in codes:
        cases = [i for i in ref if code in ref[i].critical_types]
        flagged = sum(pred[i].critical for i in cases)
        typed = sum(code in pred[i].critical_types for i in cases)
        by_type[code] = {
            "support": len(cases),
            "recall_flag": _ratio(flagged, len(cases)), "recall_flag_ci95": wilson_interval(flagged, len(cases)),
            "recall_type": _ratio(typed, len(cases)), "recall_type_ci95": wilson_interval(typed, len(cases)),
        }
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        **prf(tp, fp, fn),
        "recall_ci95": wilson_interval(tp, tp + fn),
        "precision_ci95": wilson_interval(tp, tp + fp),
        "by_type": by_type,
    }


def error_list(ref: Mapping[str, Labels], pred: Mapping[str, Labels],
               extra: Mapping[str, Mapping[str, str]] | None = None) -> list[dict[str, Any]]:
    """One entry per item with any disagreement; missed critical cases first."""
    rank = {"critical_fn": 0, "critical_fp": 1, "critical_type": 2, "sentiment": 3, "themes": 4}
    errors = []
    for i in ref:
        r, p = ref[i], pred[i]
        kinds = []
        if r.critical != p.critical:
            kinds.append("critical_fn" if r.critical else "critical_fp")
        elif r.critical and not r.critical_types <= p.critical_types:
            kinds.append("critical_type")
        if r.sentiment != p.sentiment:
            kinds.append("sentiment")
        if r.themes != p.themes:
            kinds.append("themes")
        if kinds:
            errors.append({"id": i, "kinds": kinds, "ref": _labels_dict(r), "pred": _labels_dict(p),
                           **dict((extra or {}).get(i, {}))})
    return sorted(errors, key=lambda e: (min(rank[k] for k in e["kinds"]), e["id"]))


def evaluate(ref: Mapping[str, Labels], pred: Mapping[str, Labels],
             extra: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    """All metrics over the ids present in both `ref` and `pred`."""
    common = {i: ref[i] for i in ref if i in pred}
    return {
        "n": len(common),
        "missing_predictions": sorted(i for i in ref if i not in pred),
        "sentiment": sentiment_metrics(common, pred, config.sentiment_codes()),
        "themes": theme_metrics(common, pred, config.theme_codes()),
        "critical": critical_metrics(common, pred, config.critical_codes()),
        "errors": error_list(common, pred, extra),
    }


# ----------------------------------------------------------------------- input

def _codes(value: Any) -> frozenset[str]:
    if value is None or value == "":
        return frozenset()
    if isinstance(value, str):
        return frozenset(v for v in value.split("|") if v)
    return frozenset(value)


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def to_labels(row: Mapping[str, Any]) -> Labels:
    return Labels(themes=_codes(row.get("themes")), sentiment=row.get("sentiment") or None,
                  critical=_flag(row.get("critical")), critical_types=_codes(row.get("critical_types")))


def _labels_dict(labels: Labels) -> dict[str, Any]:
    return {"themes": sorted(labels.themes), "sentiment": labels.sentiment,
            "critical": labels.critical, "critical_types": sorted(labels.critical_types)}


def read_reference(path: Path, ids: Iterable[str] | None = None) -> tuple[dict[str, Labels], dict[str, dict[str, str]]]:
    """Reference labels for rows with a comment, plus subtle/traits per id for the error list."""
    wanted = set(ids) if ids is not None else None
    ref: dict[str, Labels] = {}
    extra: dict[str, dict[str, str]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("themes") or (wanted is not None and row["id"] not in wanted):
                continue
            ref[row["id"]] = to_labels(row)
            extra[row["id"]] = {k: row[k] for k in ("subtle", "traits") if k in row}
    return ref, extra


def read_predictions(path: Path) -> dict[str, Labels]:
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {str(row["id"]): to_labels(row) for row in rows}


def read_split(path: Path, key: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        splits = json.load(fh)
    if key not in splits or not isinstance(splits[key], list):
        raise SystemExit(f"split {key!r} not found in {path}")
    return list(splits[key])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classification metrics against reference labels.")
    parser.add_argument("--pred", required=True, type=Path, help="JSONL predictions")
    parser.add_argument("--ref", required=True, type=Path, help="CSV like intended_labels.csv")
    parser.add_argument("--ids", help="key of splits.json to restrict the ids (test, dev, dev_quick)")
    parser.add_argument("--splits", type=Path, default=config.resolve("data/synthetic/splits.json"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    ids = read_split(args.splits, args.ids) if args.ids else None
    ref, extra = read_reference(args.ref, ids)
    report = {"pred": str(args.pred), "ref": str(args.ref), "ids": args.ids,
              **evaluate(ref, read_predictions(args.pred), extra)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    crit = report["critical"]
    print(f"n={report['n']} sentiment_acc={report['sentiment']['accuracy']} "
          f"macro_f1={report['themes']['macro_f1']} critical_recall={crit['recall']} "
          f"(tp={crit['tp']} fn={crit['fn']} fp={crit['fp']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
