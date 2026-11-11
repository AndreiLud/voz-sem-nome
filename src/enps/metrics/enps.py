"""eNPS: promoters score 9 or 10, passives 7 or 8, detractors 0 to 6."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def category(score: int) -> str:
    if not 0 <= score <= 10:
        raise ValueError(f"score out of range: {score}")
    if score >= 9:
        return "promoter"
    return "passive" if score >= 7 else "detractor"


def round_half_away(numerator: int, denominator: int) -> int:
    """round(numerator / denominator) with halves away from zero, in exact integer math."""
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    return sign * (quotient + (1 if 2 * remainder >= denominator else 0))


def enps_value(promoters: int, detractors: int, n: int) -> int | None:
    """round(100 * (promoters - detractors) / n); None for an empty group."""
    if n == 0:
        return None
    return round_half_away(100 * (promoters - detractors), n)


def _pct(part: int, n: int) -> float | None:
    return round(100 * part / n, 1) if n else None


def summarize(scores: Iterable[int]) -> dict[str, Any]:
    """n, eNPS and the three groups with their shares (in percent, one decimal)."""
    counts = {"promoter": 0, "passive": 0, "detractor": 0}
    for score in scores:
        counts[category(score)] += 1
    n = sum(counts.values())
    promoters, passives, detractors = counts["promoter"], counts["passive"], counts["detractor"]
    return {
        "n": n,
        "enps": enps_value(promoters, detractors, n),
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "pct_promoters": _pct(promoters, n),
        "pct_passives": _pct(passives, n),
        "pct_detractors": _pct(detractors, n),
    }
