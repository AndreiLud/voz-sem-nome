"""Respondents of the synthetic survey: area, tenure, score and submission time."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from enps import config

from .bank import Fragment
from .story import (AREA_SIZES, DAY_WEIGHTS, DETRACTOR_WEIGHTS_HARSH, DETRACTOR_WEIGHTS_MILD,
                    EIGHT_SHARE, FEMALE_SHARE, HOUR_WEIGHTS, PASSIVE_SHARE,
                    PROMOTER_TEN_SHARE_HARSH, PROMOTER_TEN_SHARE_MILD, SURVEY_END, SURVEY_START,
                    TARGET_ENPS, TENURE_EFFECT, TENURE_MIX, UTC_OFFSET_HOURS)

TZ = timezone(timedelta(hours=UTC_OFFSET_HOURS))


@dataclass
class Respondent:
    """One answer before its comment is written. `kind` says what comment it will get:
    regular, empty, short, critical, rant, near_miss, decoy or pii."""

    index: int
    area: str
    tenure: str
    score: int
    gender: str
    submitted_at: datetime
    kind: str = "regular"
    special: Fragment | None = None


def largest_remainder(total: int, shares: tuple[float, ...]) -> list[int]:
    """Integer counts that add up to `total` and follow `shares` as closely as possible."""
    raw = [total * s / sum(shares) for s in shares]
    counts = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - counts[i]), i))
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return counts


def area_scores(area: str, n: int, rng: random.Random) -> list[int]:
    """Scores whose eNPS is close to the area target (a small seeded jitter keeps it natural)."""
    target = TARGET_ENPS[area] / 100
    passive = PASSIVE_SHARE[area]
    promoters = round(n * (1 - passive + target) / 2)
    detractors = round(n * (1 - passive - target) / 2)
    if n >= 20:
        promoters += rng.choice((-1, 0, 0, 1))
        detractors += rng.choice((-1, 0, 0, 1))
    passives = n - promoters - detractors
    harsh = TARGET_ENPS[area] <= -15
    det_weights = DETRACTOR_WEIGHTS_HARSH if harsh else DETRACTOR_WEIGHTS_MILD
    ten_share = PROMOTER_TEN_SHARE_HARSH if harsh else PROMOTER_TEN_SHARE_MILD
    scores = rng.choices(range(7), weights=det_weights, k=detractors)
    scores += [8 if rng.random() < EIGHT_SHARE else 7 for _ in range(passives)]
    scores += [10 if rng.random() < ten_share else 9 for _ in range(promoters)]
    return scores


def area_tenures(area: str, n: int, rng: random.Random) -> list[str]:
    codes = config.tenure_codes()
    tenures = [code for code, count in zip(codes, largest_remainder(n, TENURE_MIX[area]))
               for _ in range(count)]
    rng.shuffle(tenures)
    return tenures


def business_days(start: date, end: date) -> list[date]:
    days = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def submission_time(rng: random.Random, days: list[date]) -> datetime:
    day = rng.choices(days, weights=DAY_WEIGHTS[: len(days)])[0]
    hours = list(HOUR_WEIGHTS)
    hour = rng.choices(hours, weights=[HOUR_WEIGHTS[h] for h in hours])[0]
    return datetime(day.year, day.month, day.day, hour, rng.randrange(60), rng.randrange(60), tzinfo=TZ)


def build_population(rng: random.Random) -> list[Respondent]:
    """All respondents, area by area. Inside an area, scores are matched to a latent mood that
    depends on tenure (newcomers happier, 1 to 3 years the least happy) plus noise."""
    days = business_days(SURVEY_START, SURVEY_END)
    respondents: list[Respondent] = []
    for area, n in AREA_SIZES.items():
        scores = sorted(area_scores(area, n, rng), reverse=True)
        tenures = area_tenures(area, n, rng)
        mood = [TENURE_EFFECT[t] + rng.gauss(0, 1) for t in tenures]
        ranking = sorted(range(n), key=lambda i: (-mood[i], i))
        assigned = [0] * n
        for rank, i in enumerate(ranking):
            assigned[i] = scores[rank]
        for i in range(n):
            gender = "f" if rng.random() < FEMALE_SHARE[area] else "m"
            respondents.append(Respondent(
                index=len(respondents), area=area, tenure=tenures[i], score=assigned[i],
                gender=gender, submitted_at=submission_time(rng, days),
            ))
    return respondents
