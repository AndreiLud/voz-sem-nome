"""Dashboard JSON for one dataset, built from the database under the minimum group rule."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from enps import config
from enps.metrics import enps
from enps.metrics.excerpt import public_excerpt
from enps.metrics.suppression import Partition, partition_groups, suppress_cells
from enps.storage.timeutil import parse_iso, sp_day, to_iso, utc_now

MAX_CRITICAL_CASES = 100  # most recent first
TOTAL_KEYS = (
    "responses", "with_comment", "enps", "promoters", "passives", "detractors",
    "pct_promoters", "pct_passives", "pct_detractors", "critical",
)


@dataclass(frozen=True)
class Record:
    public_id: str
    score: int
    area: str
    tenure: str
    comment: str
    submitted_at: str
    classified: bool
    themes: tuple[str, ...]
    sentiment: str | None
    critical: bool
    critical_types: tuple[str, ...]


def load_records(conn: sqlite3.Connection, dataset: str) -> list[Record]:
    rows = conn.execute(
        """
        SELECT r.public_id, r.score, r.area, r.tenure, r.comment_anon, r.submitted_at,
               r.status IN ('done', 'review') AND c.response_id IS NOT NULL AS classified,
               c.themes, c.sentiment, c.critical, c.critical_types
          FROM responses r LEFT JOIN classifications c ON c.response_id = r.id
         WHERE r.dataset = ?
         ORDER BY r.id
        """,
        (dataset,),
    ).fetchall()
    return [
        Record(
            public_id=row["public_id"], score=row["score"], area=row["area"],
            tenure=row["tenure"], comment=row["comment_anon"], submitted_at=row["submitted_at"],
            classified=bool(row["classified"]),
            themes=tuple(json.loads(row["themes"])) if row["classified"] else (),
            sentiment=row["sentiment"] if row["classified"] else None,
            critical=bool(row["classified"] and row["critical"]),
            critical_types=tuple(json.loads(row["critical_types"])) if row["classified"] else (),
        )
        for row in rows
    ]


def _codes(cfg: Mapping[str, Any], key: str, found: Iterable[str]) -> list[str]:
    """Configured codes first, then any unexpected code present in the data."""
    configured = [item["code"] for item in cfg["survey"][key]]
    return list(dict.fromkeys([*configured, *found]))


def group_stats(records: Sequence[Record]) -> dict[str, Any]:
    summary = enps.summarize(r.score for r in records)
    classified = [r for r in records if r.classified]
    themes = Counter(t for r in classified for t in r.themes)
    sentiment = Counter(r.sentiment for r in classified if r.sentiment)
    return {
        "n": summary["n"],
        "enps": summary["enps"],
        "promoters": summary["promoters"],
        "passives": summary["passives"],
        "detractors": summary["detractors"],
        "themes": {code: themes.get(code, 0) for code in config.theme_codes()},
        "sentiment": {code: sentiment.get(code, 0) for code in config.sentiment_codes()},
        "critical": sum(r.critical for r in records),
    }


def _dimension(
    records: Sequence[Record], attr: str, codes: Sequence[str], k: int
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, Partition]:
    groups = {code: [r for r in records if getattr(r, attr) == code] for code in codes}
    part = partition_groups({code: len(group) for code, group in groups.items()}, k)
    listed = [{"code": code, **group_stats(groups[code])} for code in part.visible]
    other = None
    if part.merged and part.other_shown:
        merged = [r for code in part.merged for r in groups[code]]
        other = {"codes": None, **group_stats(merged)}
    return listed, other, part


def _area_tenure(
    records: Sequence[Record], areas: Sequence[str], tenures: Sequence[str],
    area_part: Partition, tenure_part: Partition, k: int,
) -> list[dict[str, Any]]:
    cells: dict[tuple[str, str], list[int]] = {}
    for r in records:
        cells.setdefault((r.area, r.tenure), []).append(r.score)
    suppressed = suppress_cells(
        {cell: len(scores) for cell, scores in cells.items()}, areas, tenures, k,
        hidden_rows=[a for a in areas if a not in area_part.visible],
        hidden_cols=[t for t in tenures if t not in tenure_part.visible],
    )
    out: list[dict[str, Any]] = []
    for area in areas:
        for tenure in tenures:
            if (area, tenure) in suppressed:
                out.append({"area": area, "tenure": tenure, "suppressed": True})
            else:
                summary = enps.summarize(cells.get((area, tenure), []))
                out.append({"area": area, "tenure": tenure, "n": summary["n"],
                            "enps": summary["enps"]})
    return out


def _totals(records: Sequence[Record], k: int) -> dict[str, Any]:
    if len(records) < k:
        return {key: None for key in TOTAL_KEYS}
    summary = enps.summarize(r.score for r in records)
    return {
        "responses": summary["n"],
        "with_comment": sum(1 for r in records if r.comment),
        **{key: summary[key] for key in TOTAL_KEYS[2:-1]},
        "critical": sum(r.critical for r in records),
    }


def _themes(records: Sequence[Record]) -> list[dict[str, Any]]:
    classified = [r for r in records if r.classified]
    out = []
    for code in config.theme_codes():
        with_theme = [r for r in classified if code in r.themes]
        sentiment = Counter(r.sentiment for r in with_theme if r.sentiment)
        out.append({
            "code": code,
            "n": len(with_theme),
            "sentiment": {s: sentiment.get(s, 0) for s in config.sentiment_codes()},
            "critical": sum(r.critical for r in with_theme),
        })
    return out


def _critical(
    records: Sequence[Record], visible_areas: Iterable[str], excerpt_chars: int
) -> dict[str, Any]:
    visible = set(visible_areas)
    critical = [r for r in records if r.critical]
    by_type = Counter(t for r in critical for t in r.critical_types)
    recent = sorted(critical, key=lambda r: r.submitted_at, reverse=True)[:MAX_CRITICAL_CASES]
    return {
        "total": len(critical),
        "by_type": {code: by_type.get(code, 0) for code in config.critical_codes()},
        "cases": [
            {
                "id": r.public_id,
                "types": list(r.critical_types),
                "themes": list(r.themes),
                "area": r.area if r.area in visible else None,
                "excerpt": public_excerpt(r.comment, excerpt_chars),
                "date": sp_day(parse_iso(r.submitted_at)),
            }
            for r in recent
        ],
    }


def pipeline_info(conn: sqlite3.Connection, dataset: str) -> dict[str, Any]:
    """Model and versions of the latest classification and anonymization in the dataset."""
    model = conn.execute(
        """
        SELECT c.model, c.quantization, c.prompt_version
          FROM classifications c JOIN responses r ON r.id = c.response_id
         WHERE r.dataset = ? AND c.model IS NOT NULL
         ORDER BY c.created_at DESC, c.response_id DESC LIMIT 1
        """,
        (dataset,),
    ).fetchone()
    anonymizer = conn.execute(
        "SELECT anonymizer_version FROM responses WHERE dataset = ? AND anonymizer_version != '' "
        "ORDER BY id DESC LIMIT 1",
        (dataset,),
    ).fetchone()
    return {
        "model": model["model"] if model else None,
        "quantization": model["quantization"] if model else None,
        "prompt_version": model["prompt_version"] if model else None,
        "anonymizer_version": anonymizer["anonymizer_version"] if anonymizer else None,
    }


def visible_area_codes(
    conn: sqlite3.Connection, dataset: str, cfg: Mapping[str, Any] | None = None
) -> set[str]:
    """Areas listed by name on the dashboard of `dataset` right now."""
    cfg = cfg or config.settings()
    rows = conn.execute(
        "SELECT area, count(*) AS n FROM responses WHERE dataset = ? GROUP BY area", (dataset,)
    ).fetchall()
    found = {row["area"]: row["n"] for row in rows}
    counts = {code: found.get(code, 0) for code in _codes(cfg, "areas", found)}
    return set(partition_groups(counts, cfg["privacy"]["min_group_size"]).visible)


def build_dashboard(
    conn: sqlite3.Connection,
    dataset: str,
    *,
    cfg: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = cfg or config.settings()
    k = cfg["privacy"]["min_group_size"]
    records = load_records(conn, dataset)
    areas = _codes(cfg, "areas", (r.area for r in records))
    tenures = _codes(cfg, "tenure_bands", (r.tenure for r in records))
    by_area, other_areas, area_part = _dimension(records, "area", areas, k)
    by_tenure, other_tenures, tenure_part = _dimension(records, "tenure", tenures, k)
    return {
        "dataset": dataset,
        "generated_at": to_iso(now or utc_now()),
        "min_group_size": k,
        "totals": _totals(records, k),
        "by_area": by_area,
        "other_areas": other_areas,
        "by_tenure": by_tenure,
        "other_tenures": other_tenures,
        "area_tenure": _area_tenure(records, areas, tenures, area_part, tenure_part, k),
        "themes": _themes(records),
        "critical": _critical(records, area_part.visible, cfg["privacy"]["display_excerpt_chars"]),
        "pipeline": pipeline_info(conn, dataset),
    }
