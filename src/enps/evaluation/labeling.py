"""Blind human labeling of the test split.

`export` writes the sheet the labeler fills in: the anonymized comment (exactly what the model sees) in a shuffled
order, with no model prediction and no label from the generator. `import` reads the filled sheet back (a CSV
download of it) and writes data/labels/human_test.csv with the same columns as intended_labels.csv, so the
evaluation code compares predictions with either reference in the same way.

    python -m enps.evaluation.labeling export --out data/labels/labeling_sheet.csv
    python -m enps.evaluation.labeling import --sheet <downloaded.csv> --out data/labels/human_test.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import unicodedata
from pathlib import Path

from enps import config

SHEET_COLUMNS = [
    "id", "comentario", "tema_1", "tema_2", "tema_3", "tema_4", "sentimento",
    "critico", "tipo_critico_1", "tipo_critico_2", "duvida", "observacao",
]
YES, NO = "Sim", "Não"
SHUFFLE_SEED = 1611


def _labels(kind: str) -> dict[str, str]:
    """code -> Portuguese label for themes, sentiments or critical types."""
    return {item["code"]: item["label"]["pt"] for item in config.taxonomy()[kind]}


def dropdowns() -> dict[str, list[str]]:
    """Allowed values per sheet column, in taxonomy order."""
    themes = list(_labels("themes").values())
    crit = list(_labels("critical_types").values())
    return {
        "tema_1": themes, "tema_2": themes, "tema_3": themes, "tema_4": themes,
        "sentimento": list(_labels("sentiments").values()),
        "critico": [YES, NO],
        "tipo_critico_1": crit, "tipo_critico_2": crit,
        "duvida": [YES],
    }


def _read_responses(path: Path) -> dict[str, dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return {row["id"]: row for row in csv.DictReader(fh)}


def export_sheet(responses_csv: Path, splits_json: Path, out: Path) -> list[dict[str, str]]:
    from enps.anonymize import anonymize

    responses = _read_responses(responses_csv)
    test_ids = json.loads(splits_json.read_text(encoding="utf-8"))["test"]
    order = sorted(test_ids)
    random.Random(SHUFFLE_SEED).shuffle(order)
    rows = []
    for sid in order:
        text = anonymize(responses[sid]["comment"]).text
        rows.append({col: "" for col in SHEET_COLUMNS} | {"id": sid, "comentario": text})
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _reverse(kind: str) -> dict[str, str]:
    return {_norm(label): code for code, label in _labels(kind).items()}


def import_sheet(sheet_csv: Path, out: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Convert the filled sheet into reference labels. Returns the rows and a list of problems found."""
    themes, sents, crits = _reverse("themes"), _reverse("sentiments"), _reverse("critical_types")
    problems: list[str] = []
    rows: list[dict[str, str]] = []
    with open(sheet_csv, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sid = row["id"].strip()
            tcodes = []
            for col in ("tema_1", "tema_2", "tema_3", "tema_4"):
                value = _norm(row.get(col, ""))
                if not value:
                    continue
                if value not in themes:
                    problems.append(f"{sid}: unknown theme {row[col]!r}")
                elif themes[value] not in tcodes:
                    tcodes.append(themes[value])
            sentiment = sents.get(_norm(row.get("sentimento", "")), "")
            if not sentiment:
                problems.append(f"{sid}: missing or unknown sentiment")
            if not tcodes:
                problems.append(f"{sid}: no theme")
            if "out" in tcodes and len(tcodes) > 1:
                problems.append(f"{sid}: 'Outro' together with other themes")
            ccodes = []
            for col in ("tipo_critico_1", "tipo_critico_2"):
                value = _norm(row.get(col, ""))
                if value and value in crits and crits[value] not in ccodes:
                    ccodes.append(crits[value])
                elif value:
                    problems.append(f"{sid}: unknown critical type {row[col]!r}")
            critical = _norm(row.get("critico", "")) == _norm(YES)
            if critical and not ccodes:
                problems.append(f"{sid}: critical without a type")
            if ccodes and not critical:
                problems.append(f"{sid}: critical type set but 'critico' is not Sim")
                critical = True  # the type is the stronger signal; the problem is reported
            rows.append({
                "id": sid,
                "themes": "|".join(tcodes),
                "sentiment": sentiment,
                "critical": "1" if critical else "0",
                "critical_types": "|".join(ccodes),
                "doubt": "1" if _norm(row.get("duvida", "")) == _norm(YES) else "0",
            })
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "themes", "sentiment", "critical", "critical_types", "doubt"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: r["id"]))
    return rows, problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--responses", default="data/synthetic/responses.csv")
    exp.add_argument("--splits", default="data/synthetic/splits.json")
    exp.add_argument("--out", default="data/labels/labeling_sheet.csv")
    imp = sub.add_parser("import")
    imp.add_argument("--sheet", required=True)
    imp.add_argument("--out", default="data/labels/human_test.csv")
    args = parser.parse_args()
    if args.cmd == "export":
        rows = export_sheet(config.resolve(args.responses), config.resolve(args.splits), config.resolve(args.out))
        print(f"wrote {len(rows)} rows to {args.out}")
    else:
        rows, problems = import_sheet(Path(args.sheet), config.resolve(args.out))
        print(f"wrote {len(rows)} rows to {args.out}; {len(problems)} problems")
        for p in problems:
            print("  ", p)


if __name__ == "__main__":
    main()
