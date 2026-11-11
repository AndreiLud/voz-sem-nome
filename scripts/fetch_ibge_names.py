"""Download the IBGE Censo 2022 name rankings used by the anonymizer gazetteer.

Usage:
    python scripts/fetch_ibge_names.py [--first-names 3000] [--surnames 1500] [--out data/reference]

This is the only place in the project that calls the IBGE API. The anonymizer reads the CSV
files written here and never touches the network at runtime.
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import httpx

BASE_URL = "https://servicodados.ibge.gov.br/api/v3/nomes/2022/localidade/0/ranking/{kind}"
PAGE_SIZE = 30
PAUSE_S = 0.2
ROOT = Path(__file__).resolve().parents[1]


def fetch_ranking(client: httpx.Client, kind: str, limit: int) -> list[dict]:
    """Fetch the first `limit` entries of a ranking ("nome" or "sobrenome")."""
    rows: list[dict] = []
    page = 1
    while len(rows) < limit:
        response = client.get(BASE_URL.format(kind=kind), params={"page": page})
        response.raise_for_status()
        items = response.json()["items"]
        if not items:
            break
        rows.extend(
            {"name": item["nome"], "frequency": item["frequencia"], "rank": item["rank"]}
            for item in items
        )
        page += 1
        time.sleep(PAUSE_S)
    return rows[:limit]


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "frequency", "rank"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first-names", type=int, default=3000)
    parser.add_argument("--surnames", type=int, default=1500)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "reference")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30.0) as client:
        first = fetch_ranking(client, "nome", args.first_names)
        write_csv(args.out / "ibge_first_names_2022.csv", first)
        surnames = fetch_ranking(client, "sobrenome", args.surnames)
        write_csv(args.out / "ibge_surnames_2022.csv", surnames)
    print(f"first names: {len(first)}, surnames: {len(surnames)} -> {args.out}")


if __name__ == "__main__":
    main()
