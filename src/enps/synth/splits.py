"""Evaluation splits: a stratified test set, the rest as dev, and a quick subset of dev."""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from enps import config

from .story import AREA_SIZES, SPLIT_QUICK_SIZE, SPLIT_TEST_SIZE


@dataclass(frozen=True)
class Item:
    """What the split logic needs to know about one non-empty comment."""

    id: str
    area: str
    themes: tuple[str, ...]
    critical_types: tuple[str, ...]
    near_miss: bool
    pii: bool
    short: bool


@dataclass(frozen=True)
class Quotas:
    size: int
    critical: int     # critical comments in total
    per_type: int     # minimum per critical type
    near_miss: int
    pii: int
    short: int
    per_theme: int


TEST_QUOTAS = Quotas(SPLIT_TEST_SIZE, critical=20, per_type=3, near_miss=5, pii=12, short=8, per_theme=6)
QUICK_QUOTAS = Quotas(SPLIT_QUICK_SIZE, critical=8, per_type=1, near_miss=3, pii=4, short=2, per_theme=1)


class _Picker:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.picked: list[Item] = []
        self._ids: set[str] = set()

    def has(self, item: Item) -> bool:
        return item.id in self._ids

    def add_from(self, cands: list[Item]) -> bool:
        cands = [c for c in cands if not self.has(c)]
        if not cands:
            return False
        item = self.rng.choice(cands)
        self.picked.append(item)
        self._ids.add(item.id)
        return True

    def count(self, pred: Callable[[Item], bool]) -> int:
        return sum(1 for i in self.picked if pred(i))

    def reach(self, minimum: int, pred: Callable[[Item], bool], pool: list[Item], what: str,
              allowed: Callable[[Item], bool] = lambda _: True) -> None:
        """Add items from `pool` until `minimum` picked items match `pred`. `allowed` is
        checked again before every pick."""
        while self.count(pred) < minimum:
            if not self.add_from([i for i in pool if pred(i) and allowed(i)]):
                raise ValueError(f"not enough comments for the split: {what}")

    def fill_by_area(self, size: int, pool: list[Item]) -> None:
        """Top up to `size`, always from the area most under-represented so far."""
        total = sum(AREA_SIZES.values())
        while len(self.picked) < size:
            counts = Counter(i.area for i in self.picked)
            open_areas = [a for a in AREA_SIZES if any(i.area == a and not self.has(i) for i in pool)]
            if not open_areas:
                raise ValueError("not enough comments to fill the split")
            area = max(open_areas, key=lambda a: (AREA_SIZES[a] / total * size - counts[a],
                                                  -list(AREA_SIZES).index(a)))
            self.add_from([i for i in pool if i.area == area])


def _critical_picks(picker: _Picker, critical: list[Item], quotas: Quotas, keep_out: int) -> None:
    """At least `per_type` cases of each type, then `critical` cases in total, always leaving
    `keep_out` cases of every type outside this split."""
    available = Counter(c for i in critical for c in i.critical_types)

    def allowed(item: Item) -> bool:
        taken = Counter(c for i in picker.picked for c in i.critical_types)
        return all(available[c] - taken[c] - 1 >= keep_out for c in item.critical_types)

    for code in config.critical_codes():
        picker.reach(quotas.per_type, lambda i, c=code: c in i.critical_types, critical,
                     f"critical type {code}", allowed)
    picker.reach(quotas.critical, lambda i: bool(i.critical_types), critical, "critical cases", allowed)


def _split(pool: list[Item], quotas: Quotas, rng: random.Random, keep_out: int) -> list[Item]:
    picker = _Picker(rng)
    _critical_picks(picker, [i for i in pool if i.critical_types], quotas, keep_out)
    picker.reach(quotas.near_miss, lambda i: i.near_miss, pool, "near misses")
    plain = [i for i in pool if not i.critical_types and not i.near_miss]
    picker.reach(quotas.pii, lambda i: i.pii, plain, "personal data")
    picker.reach(quotas.short, lambda i: i.short, plain, "short comments")
    for theme in config.theme_codes():
        picker.reach(quotas.per_theme, lambda i, t=theme: t in i.themes, plain, f"theme {theme}")
    picker.fill_by_area(quotas.size, plain)
    if len(picker.picked) != quotas.size:
        raise ValueError(f"split has {len(picker.picked)} comments instead of {quotas.size}")
    return picker.picked


def make_splits(items: list[Item], rng: random.Random) -> dict[str, list[str]]:
    """Ids of the test, dev and dev_quick splits (all sorted). The test split leaves at least
    one case of each critical type in dev, so dev_quick can have every type."""
    test = _split(items, TEST_QUOTAS, rng, keep_out=QUICK_QUOTAS.per_type)
    test_ids = {i.id for i in test}
    dev = [i for i in items if i.id not in test_ids]
    quick = _split(dev, QUICK_QUOTAS, rng, keep_out=0)
    return {
        "test": sorted(test_ids),
        "dev": sorted(i.id for i in dev),
        "dev_quick": sorted(i.id for i in quick),
    }
