"""Decide which respondent gets which kind of comment.

Order matters: critical cases first (they need low scores in the right areas), then empty and
short answers, then the other special templates (rants, near misses, decoys, personal data).
Everyone left gets a regular comment composed from random fragments.
"""
from __future__ import annotations

import random
from collections import Counter
from typing import Callable

from enps import config

from .bank import Fragment, PhraseBank
from .population import Respondent
from .story import (CRITICAL_AREAS, CRITICAL_EXPLICIT, CRITICAL_MULTI, CRITICAL_TYPES,
                    EMPTY_SHARE, NEAR_MISS_CASES, PII_SHARE, PII_TARGETS, SELF_HARM_CASES,
                    SHORT_SHARE, score_group)

# How well a template's polarity fits a score group.
COMPAT = {
    "pos": {"pro": 4.0, "pas": 2.0, "det": 0.4},
    "neg": {"pro": 0.5, "pas": 1.5, "det": 3.0},
    "neu": {"pro": 1.0, "pas": 1.5, "det": 1.0},
}


def compat_weight(frag: Fragment, resp: Respondent) -> float:
    return COMPAT[frag.polarity][score_group(resp.score)]


def critical_weight(frag: Fragment, resp: Respondent) -> float:
    """Critical cases come mostly from low scores, but not only. Self-harm mentions only
    come with detractor scores."""
    if resp.score <= 4:
        return 6.0
    if resp.score <= 6:
        return 4.0
    if frag.self_harm:
        return 0.0
    if resp.score <= 8:
        return 1.2
    return 0.15


def rant_weight(_: Fragment, resp: Respondent) -> float:
    return {0: 5.0, 1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0, 6: 0.5, 7: 0.1}.get(resp.score, 0.0)


def _free(respondents: list[Respondent]) -> list[Respondent]:
    return [r for r in respondents if r.kind == "regular"]


def assign(templates: list[Fragment], respondents: list[Respondent], kind: str,
           rng: random.Random, weight: Callable[[Fragment, Respondent], float],
           area: dict[str, str] | None = None) -> None:
    """Give each template to one free respondent it fits, most constrained templates first."""
    def candidates(frag: Fragment) -> list[Respondent]:
        wanted = area.get(frag.key) if area else None
        return [r for r in _free(respondents) if frag.fits(r.area, r.tenure, r.gender)
                and (wanted is None or r.area == wanted) and weight(frag, r) > 0]

    order = sorted(templates, key=lambda f: (len(candidates(f)), f.key))
    for frag in order:
        cands = candidates(frag)
        if not cands and area:
            cands = [r for r in _free(respondents) if frag.fits(r.area, r.tenure, r.gender)
                     and weight(frag, r) > 0]
        if not cands:
            raise ValueError(f"no free respondent fits template {frag.key}")
        chosen = rng.choices(cands, weights=[weight(frag, r) for r in cands])[0]
        chosen.kind, chosen.special = kind, frag


def select_critical(bank: PhraseBank, rng: random.Random) -> list[Fragment]:
    """Critical templates that match the per-type quotas, the explicit share and the number of
    self-harm mentions. Each template is used at most once."""
    multi = [f for f in bank.critical if len(f.critical) > 1]
    chosen = rng.sample(multi, CRITICAL_MULTI)
    need = dict(CRITICAL_TYPES)
    for frag in chosen:
        for code in frag.critical:
            need[code] -= 1
    for code in config.critical_codes():
        singles = [f for f in bank.critical if f.critical == (code,)]
        explicit = [f for f in singles if not f.subtle]
        self_harm = [f for f in singles if f.self_harm]
        subtle = [f for f in singles if f.subtle and not f.self_harm]
        n_explicit = min(CRITICAL_EXPLICIT[code], need[code])
        n_self_harm = SELF_HARM_CASES if code == "bo" else 0
        chosen += rng.sample(explicit, n_explicit)
        chosen += rng.sample(self_harm, n_self_harm)
        chosen += rng.sample(subtle, need[code] - n_explicit - n_self_harm)
    return chosen


def critical_areas(templates: list[Fragment], rng: random.Random) -> dict[str, str]:
    """Area of each critical case. Area-specific templates go to their area; the others fill
    the per-area quotas, so cases concentrate in customer service and operations."""
    remaining = dict(CRITICAL_AREAS)
    areas: dict[str, str] = {}
    restricted = sorted((f for f in templates if f.areas), key=lambda f: (len(f.areas), f.key))
    for frag in restricted:
        open_areas = [a for a in frag.areas if remaining[a] > 0]
        if open_areas:
            area = rng.choices(open_areas, weights=[remaining[a] for a in open_areas])[0]
            remaining[area] -= 1
        else:  # the area is full: the case still goes there and the largest quota shrinks
            area = rng.choice(frag.areas)
            largest = max(remaining, key=lambda a: (remaining[a], a))
            remaining[largest] -= 1
        areas[frag.key] = area
    pool = [a for a, n in remaining.items() for _ in range(max(n, 0))]
    rng.shuffle(pool)
    for frag, area in zip(sorted((f for f in templates if not f.areas), key=lambda f: f.key), pool):
        areas[frag.key] = area
    return areas


def select_pii(bank: PhraseBank, rng: random.Random, already: Counter[str], already_comments: int,
               total: int) -> list[Fragment]:
    """Greedy cover of the per-type span targets, then a top-up to the wanted comment count."""
    need = {t: max(0, n - already.get(t, 0)) for t, n in PII_TARGETS.items()}
    tiebreak = {f.key: rng.random() for f in bank.pii}
    pool = list(bank.pii)
    chosen: list[Fragment] = []

    def gain(frag: Fragment) -> int:
        return sum(min(c, need.get(t, 0)) for t, c in frag.pii_counts().items())

    while any(need.values()):
        best = max(pool, key=lambda f: (gain(f), tiebreak[f.key]))
        if gain(best) == 0:
            raise ValueError("the phrase bank cannot reach the personal data targets")
        chosen.append(best)
        pool.remove(best)
        for t, c in best.pii_counts().items():
            need[t] = max(0, need.get(t, 0) - c)
    pool.sort(key=lambda f: tiebreak[f.key])
    while len(chosen) + already_comments < total and pool:
        chosen.append(pool.pop())
    return chosen


def plan(respondents: list[Respondent], bank: PhraseBank, rng: random.Random) -> None:
    """Set `kind` and `special` on every respondent."""
    n = len(respondents)
    critical = select_critical(bank, rng)
    assign(critical, respondents, "critical", rng, critical_weight, critical_areas(critical, rng))

    free = _free(respondents)
    empty_weight = {"pro": 1.1, "pas": 1.4, "det": 0.9}
    for r in _weighted_sample(free, [empty_weight[score_group(r.score)] for r in free],
                              round(EMPTY_SHARE * n), rng):
        r.kind = "empty"
    short_usage: Counter[str] = Counter()
    for r in rng.sample(_free(respondents), round(SHORT_SHARE * n)):
        r.kind = "short"
        r.special = _short_item(bank, r, rng, short_usage)

    assign(list(bank.rants), respondents, "rant", rng, rant_weight)
    assign(rng.sample(list(bank.near_miss), NEAR_MISS_CASES), respondents, "near_miss", rng, compat_weight)
    assign(list(bank.decoys), respondents, "decoy", rng, compat_weight)

    planted: Counter[str] = Counter()
    with_pii = 0
    for frag in critical:
        planted.update(frag.pii_counts())
        with_pii += bool(frag.slots())
    pii = select_pii(bank, rng, planted, with_pii, round(PII_SHARE * n))
    assign(pii, respondents, "pii", rng, compat_weight)


def _short_item(bank: PhraseBank, resp: Respondent, rng: random.Random,
                usage: Counter[str]) -> Fragment:
    items = [f for f in bank.short if f.fits(resp.area, resp.tenure, resp.gender)]
    weights = [compat_weight(f, resp) / (1 + usage[f.key]) ** 2 for f in items]
    item = rng.choices(items, weights=weights)[0]
    usage[item.key] += 1
    return item


def _weighted_sample(items: list[Respondent], weights: list[float], k: int,
                     rng: random.Random) -> list[Respondent]:
    """k distinct items drawn with probability proportional to weight (Efraimidis-Spirakis)."""
    keyed = sorted(range(len(items)), key=lambda i: -(rng.random() ** (1 / weights[i])))
    return [items[i] for i in keyed[:k]]
