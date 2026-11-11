"""Latency and throughput of the local model, plus the hardware it ran on.

The first item of a run pays for loading the model and for evaluating the whole system prompt;
after that the runtime reuses the prompt prefix. Mixing both hides the steady-state cost, so the
cold start is reported on its own.

    python -m enps.evaluation.performance --records timings.jsonl --out metrics/performance.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from enps.classify.types import ClassificationResult

FIELDS = ("total_ms", "load_ms", "prompt_tokens", "cached_tokens", "output_tokens",
          "prompt_ms", "eval_ms")
CPU_FLAGS = ("sse4_2", "avx", "avx2", "fma")


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (the numpy default)."""
    if not values:
        return None
    data = sorted(values)
    pos = (len(data) - 1) * q / 100
    low, high = math.floor(pos), math.ceil(pos)
    return data[low] + (data[high] - data[low]) * (pos - low)


def _rate(tokens: int, ms: int) -> float | None:
    return tokens / (ms / 1000) if ms > 0 else None


def record_from_result(result: ClassificationResult) -> dict[str, int]:
    """Per-item record: the sum of every model stage of one classification."""
    return {f: sum(getattr(s, f) for s in result.stages) for f in FIELDS}


def _get(record: Mapping[str, Any], field: str) -> int:
    return int(record.get(field) or 0)


def steady_stats(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    seconds = [_get(r, "total_ms") / 1000 for r in records]
    sums = {f: sum(_get(r, f) for r in records) for f in FIELDS}
    evaluated = sums["prompt_tokens"] - sums["cached_tokens"]
    return {
        "n": len(records),
        "total_s": sum(seconds),
        "mean_s": sum(seconds) / len(seconds) if seconds else None,
        "p50_s": percentile(seconds, 50),
        "p95_s": percentile(seconds, 95),
        "max_s": max(seconds) if seconds else None,
        "generation_tps": _rate(sums["output_tokens"], sums["eval_ms"]),
        "prompt_tps": _rate(evaluated, sums["prompt_ms"]),
        "prompt_tokens": sums["prompt_tokens"],
        "cached_tokens": sums["cached_tokens"],
        "cache_ratio": sums["cached_tokens"] / sums["prompt_tokens"] if sums["prompt_tokens"] else None,
        "output_tokens": sums["output_tokens"],
    }


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Cold start (first item) apart from the steady state (all the others)."""
    if not records:
        return {"cold_start": None, "steady": steady_stats([])}
    first = records[0]
    cold = {
        "total_s": _get(first, "total_ms") / 1000,
        "load_s": _get(first, "load_ms") / 1000,
        "prompt_tokens": _get(first, "prompt_tokens"),
        "cached_tokens": _get(first, "cached_tokens"),
        "prompt_tps": _rate(_get(first, "prompt_tokens") - _get(first, "cached_tokens"),
                            _get(first, "prompt_ms")),
    }
    return {"cold_start": cold, "steady": steady_stats(records[1:])}


# --------------------------------------------------------------------- hardware

def _parse_cpuinfo(text: str) -> dict[str, Any]:
    blocks = [b for b in text.split("\n\n") if b.strip()]
    entries = [dict(_split(line) for line in b.splitlines() if ":" in line) for b in blocks]
    first = entries[0] if entries else {}
    flags = set(first.get("flags", "").split())
    physical = {(e.get("physical id"), e.get("core id")) for e in entries if "core id" in e}
    return {
        "model_name": first.get("model name"),
        "logical_cpus": sum("processor" in e for e in entries),
        "physical_cores": len(physical) or None,
        "flags": {flag: flag in flags for flag in CPU_FLAGS},
    }


def _split(line: str) -> tuple[str, str]:
    key, _, value = line.partition(":")
    return key.strip(), value.strip()


def _parse_meminfo(text: str) -> float | None:
    for line in text.splitlines():
        key, value = _split(line)
        if key == "MemTotal":
            return round(int(value.split()[0]) / 1024 / 1024, 1)
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def hardware_spec(cpuinfo: Path = Path("/proc/cpuinfo"),
                  meminfo: Path = Path("/proc/meminfo")) -> dict[str, Any]:
    """CPU model, cores, SIMD flags and total memory, read from /proc only."""
    return {
        **_parse_cpuinfo(_read(cpuinfo)),
        "mem_total_gib": _parse_meminfo(_read(meminfo)),
        "os_cpu_count": os.cpu_count(),
        "machine": platform.machine(),
    }


def _read_records(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Latency summary of a classification run.")
    parser.add_argument("--records", required=True, type=Path, help="JSONL, one timing record per item")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = {**summarize(_read_records(args.records)), "hardware": hardware_spec()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["steady"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
