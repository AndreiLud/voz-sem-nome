"""Data shown by the static pages: evaluation metrics, the dashboard preview and the estimator.

Inputs may be missing or partial. A missing file falls back to a fixture under
site/src/fixtures/ marked as sample data; a missing number inside a real file is shown
as "not measured yet". Nothing here invents a number.

Expected shape of metrics/metrics.json (every key optional):

    {
      "generated_at": ISO 8601,
      "anonymization": {"n_spans", "recall", "recall_ci95": [lo, hi], "precision",
                        "n_comments", "comments_with_leak"},
      "classification": output of enps.evaluation.classification.evaluate, plus
                        "reference": "intended" | "human",
      "performance": output of enps.evaluation.performance (cold_start, steady, hardware),
      "pipeline": {"model", "quantization", "prompt_version", "anonymizer_version"}
    }
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from enps import config
from enps.export.site_content import SRC_DIR, PUBLIC_DIR

FIXTURES_DIR = SRC_DIR / "fixtures"
METRICS_PATH = config.ROOT / "metrics" / "metrics.json"
DASHBOARD_SYNTHETIC_PATH = PUBLIC_DIR / "data" / "dashboard-synthetic.json"


@dataclass(frozen=True)
class Loaded:
    """A JSON document and whether it came from a sample fixture."""
    data: dict[str, Any]
    sample: bool
    raw: bytes = b""


def _read_json(path: Path) -> tuple[dict[str, Any], bytes] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data, raw


def load_with_fallback(path: Path, fixture: Path) -> Loaded:
    """Real file when present and not itself a sample, else the fixture."""
    found = _read_json(path)
    if found is not None and not found[0].get("sample"):
        return Loaded(found[0], sample=False, raw=found[1])
    data, raw = _read_json(fixture) or ({}, b"")
    return Loaded(data, sample=True, raw=raw)


def dig(data: Any, path: str) -> Any:
    """Value at a dotted path, or None when any step is missing."""
    for part in path.split("."):
        if not isinstance(data, Mapping) or part not in data:
            return None
        data = data[part]
    return data


def first(data: Any, *paths: str) -> Any:
    for path in paths:
        value = dig(data, path)
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sum(*values: Any) -> float | None:
    numbers = [_number(v) for v in values]
    return None if any(v is None for v in numbers) else sum(numbers)  # type: ignore[arg-type]


def _interval(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = _number(value[0]), _number(value[1])
        if lo is not None and hi is not None:
            return lo, hi
    return None


# ------------------------------------------------------------------- results


@dataclass(frozen=True)
class ResultItem:
    """One number of the results section."""
    key: str
    kind: str                         # "share" (0..1), "seconds", "score" (0..1, two decimals)
    value: Callable[[Any], Any]
    n: Callable[[Any], Any]
    ci: Callable[[Any], Any] = lambda m: None
    extra: Callable[[Any], Any] = lambda m: None
    extra_kind: str = ""


def _leak_share(m: Any) -> float | None:
    leaks = _number(dig(m, "anonymization.comments_with_leak"))
    total = _number(dig(m, "anonymization.n_comments"))
    return leaks / total if leaks is not None and total else None


RESULT_ITEMS: tuple[ResultItem, ...] = (
    ResultItem("pii_recall", "share",
               value=lambda m: first(m, "anonymization.recall", "anonymization.overall.recall"),
               n=lambda m: first(m, "anonymization.n_spans", "anonymization.overall.support"),
               ci=lambda m: first(m, "anonymization.recall_ci95", "anonymization.overall.recall_ci95")),
    ResultItem("pii_leaks", "share", value=_leak_share,
               n=lambda m: dig(m, "anonymization.n_comments")),
    ResultItem("critical_recall", "share",
               value=lambda m: dig(m, "classification.critical.recall"),
               n=lambda m: _sum(dig(m, "classification.critical.tp"), dig(m, "classification.critical.fn")),
               ci=lambda m: dig(m, "classification.critical.recall_ci95")),
    ResultItem("critical_precision", "share",
               value=lambda m: dig(m, "classification.critical.precision"),
               n=lambda m: _sum(dig(m, "classification.critical.tp"), dig(m, "classification.critical.fp")),
               ci=lambda m: dig(m, "classification.critical.precision_ci95")),
    ResultItem("sentiment", "share",
               value=lambda m: dig(m, "classification.sentiment.accuracy"),
               n=lambda m: dig(m, "classification.sentiment.n"),
               extra=lambda m: dig(m, "classification.sentiment.kappa"), extra_kind="score"),
    ResultItem("themes", "score",
               value=lambda m: dig(m, "classification.themes.macro_f1"),
               n=lambda m: dig(m, "classification.n")),
    ResultItem("latency", "seconds",
               value=lambda m: dig(m, "performance.steady.p50_s"),
               n=lambda m: dig(m, "performance.steady.n"),
               extra=lambda m: dig(m, "performance.steady.p95_s"), extra_kind="seconds"),
)


@dataclass(frozen=True)
class ResultValue:
    key: str
    kind: str
    value: float | None
    n: int | None
    ci: tuple[float, float] | None = None
    extra: float | None = None
    extra_kind: str = ""

    @property
    def measured(self) -> bool:
        return self.value is not None


def result_values(metrics: Mapping[str, Any]) -> list[ResultValue]:
    out = []
    for item in RESULT_ITEMS:
        n = _number(item.n(metrics))
        out.append(ResultValue(
            key=item.key, kind=item.kind, value=_number(item.value(metrics)),
            n=int(n) if n is not None else None, ci=_interval(item.ci(metrics)),
            extra=_number(item.extra(metrics)), extra_kind=item.extra_kind,
        ))
    return out


def machine_seconds_per_comment(metrics: Mapping[str, Any]) -> float | None:
    """Mean steady-state classification time, used by the estimator."""
    return _number(first(metrics, "performance.steady.mean_s", "performance.steady.p50_s"))


def hardware(metrics: Mapping[str, Any]) -> dict[str, Any] | None:
    cpus = _number(first(metrics, "performance.hardware.logical_cpus", "performance.hardware.os_cpu_count"))
    mem = _number(dig(metrics, "performance.hardware.mem_total_gib"))
    if cpus is None or mem is None:
        return None
    return {"cpus": int(cpus), "mem": mem}


def reference_kind(metrics: Mapping[str, Any]) -> str | None:
    ref = dig(metrics, "classification.reference")
    if ref in ("intended", "human"):
        return str(ref)
    return "intended" if dig(metrics, "classification") else None


# ---------------------------------------------------------------- estimator


ESTIMATOR_FIELDS: tuple[tuple[str, str, float, float, float], ...] = (
    # key, kind, min, max, step (shares are edited as percentages)
    ("responses_per_cycle", "count", 1, 1_000_000, 1),
    ("comment_share", "share", 0, 100, 1),
    ("minutes_read_classify", "minutes", 0, 120, 0.1),
    ("minutes_anonymize", "minutes", 0, 120, 0.1),
    ("cycles_per_year", "count", 1, 52, 1),
    ("review_share", "share", 0, 100, 1),
    ("minutes_per_review", "minutes", 0, 120, 0.1),
)


@dataclass(frozen=True)
class Estimate:
    comments: float
    manual_hours: float
    review_hours: float
    saved_hours: float
    machine_hours: float | None


def estimate(values: Mapping[str, float], machine_s: float | None) -> Estimate:
    """Hours per year. Mirrors site/src/static/js/estimator.js; keep both in step."""
    comments = values["responses_per_cycle"] * values["comment_share"] * values["cycles_per_year"]
    manual = comments * (values["minutes_read_classify"] + values["minutes_anonymize"]) / 60
    review = comments * values["review_share"] * values["minutes_per_review"] / 60
    machine = comments * machine_s / 3600 if machine_s is not None else None
    return Estimate(comments, manual, review, manual - review, machine)


# ------------------------------------------------------------ dashboard preview


@dataclass(frozen=True)
class Preview:
    """Headline numbers of a dashboard JSON, or empty when below the minimum group."""
    enps: float | None
    responses: int | None
    segments: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str | None = None


def dashboard_preview(dashboard: Mapping[str, Any]) -> Preview:
    totals = dashboard.get("totals") or {}
    enps, n = _number(totals.get("enps")), _number(totals.get("responses"))
    if enps is None or not n:
        return Preview(None, None, generated_at=dashboard.get("generated_at"))
    segments = []
    for key in ("promoters", "passives", "detractors"):
        count = _number(totals.get(key)) or 0.0
        pct = _number(totals.get(f"pct_{key}"))  # percent with one decimal, per the contract
        segments.append({"key": key, "count": int(count),
                         "share": pct / 100 if pct is not None else count / n})
    return Preview(enps, int(n), segments, dashboard.get("generated_at"))
