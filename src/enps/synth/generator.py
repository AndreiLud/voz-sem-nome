"""Build the synthetic dataset and write it to data/synthetic/."""
from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

from enps import config

from .bank import PhraseBank, load_bank
from .compose import Composed, Composer
from .pii import FakeData
from .plan import plan
from .population import Respondent, build_population
from .splits import Item, make_splits
from .story import DEFAULT_SEED

DEFAULT_OUT = config.ROOT / "data" / "synthetic"
RESPONSES_HEADER = ("id", "submitted_at", "score", "area", "tenure", "comment")
LABELS_HEADER = ("id", "themes", "sentiment", "critical", "critical_types", "subtle", "traits")


@dataclass
class Record:
    id: str
    respondent: Respondent
    comment: Composed

    @property
    def submitted_at(self) -> str:
        return self.respondent.submitted_at.isoformat()


@dataclass
class Dataset:
    seed: int
    records: list[Record]
    splits: dict[str, list[str]]
    reuse: dict[str, int]          # fragment key -> times used


def _stream(seed: int, name: str) -> random.Random:
    """An independent random stream per stage, so a change in one stage does not shift the others."""
    return random.Random(f"{seed}:{name}")


def generate(seed: int = DEFAULT_SEED, bank: PhraseBank | None = None) -> Dataset:
    bank = bank or load_bank()
    respondents = build_population(_stream(seed, "population"))
    plan(respondents, bank, _stream(seed, "plan"))
    composer = Composer(bank, _stream(seed, "compose"), FakeData(_stream(seed, "pii"), bank.people, bank.places))
    comments = [composer.compose(r) for r in respondents]
    order = sorted(range(len(respondents)), key=lambda i: (respondents[i].submitted_at, i))
    records = [Record(f"S{n:04d}", respondents[i], comments[i]) for n, i in enumerate(order, start=1)]
    items = [
        Item(id=rec.id, area=rec.respondent.area, themes=tuple(rec.comment.themes),
             critical_types=tuple(rec.comment.critical_types),
             near_miss=rec.respondent.kind == "near_miss", pii=bool(rec.comment.spans),
             short=rec.respondent.kind == "short")
        for rec in records if rec.comment.text
    ]
    splits = make_splits(items, _stream(seed, "splits"))
    return Dataset(seed=seed, records=records, splits=splits, reuse=dict(composer.usage))


def write_dataset(ds: Dataset, out_dir: Path | str = DEFAULT_OUT) -> list[Path]:
    """Write the four files. Same dataset, same bytes."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / "responses.csv", out / "intended_labels.csv", out / "pii_gold.jsonl", out / "splits.json"]

    with open(paths[0], "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(RESPONSES_HEADER)
        for rec in ds.records:
            r = rec.respondent
            writer.writerow((rec.id, rec.submitted_at, r.score, r.area, r.tenure, rec.comment.text))

    with open(paths[1], "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(LABELS_HEADER)
        for rec in ds.records:
            c = rec.comment
            writer.writerow((rec.id, "|".join(c.themes), c.sentiment, int(bool(c.critical_types)),
                             "|".join(c.critical_types), int(c.subtle), "|".join(c.traits)))

    with open(paths[2], "w", encoding="utf-8", newline="\n") as fh:
        for rec in ds.records:
            if rec.comment.spans:
                fh.write(json.dumps({"id": rec.id, "spans": rec.comment.spans}, ensure_ascii=False) + "\n")

    with open(paths[3], "w", encoding="utf-8", newline="\n") as fh:
        payload = {"seed": ds.seed, **ds.splits}
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return paths
