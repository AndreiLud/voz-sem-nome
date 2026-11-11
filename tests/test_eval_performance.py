import json

import pytest

from enps.classify.types import ClassificationResult, StageRecord
from enps.evaluation.performance import (
    hardware_spec, main, percentile, record_from_result, summarize,
)

CPUINFO = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Celeron(R) CPU J4125 @ 2.00GHz
physical id\t: 0
core id\t\t: 0
flags\t\t: fpu sse sse2 ssse3 sse4_1 sse4_2 popcnt

processor\t: 1
model name\t: Intel(R) Celeron(R) CPU J4125 @ 2.00GHz
physical id\t: 0
core id\t\t: 1
flags\t\t: fpu sse sse2 ssse3 sse4_1 sse4_2 popcnt

processor\t: 2
model name\t: Intel(R) Celeron(R) CPU J4125 @ 2.00GHz
physical id\t: 0
core id\t\t: 1
flags\t\t: fpu sse sse2 ssse3 sse4_1 sse4_2 popcnt
"""
MEMINFO = "MemTotal:        7864320 kB\nMemFree:         1000000 kB\n"


def rec(total_ms, load_ms=0, prompt=1500, cached=1400, output=30, prompt_ms=5000, eval_ms=3000):
    return {"total_ms": total_ms, "load_ms": load_ms, "prompt_tokens": prompt, "cached_tokens": cached,
            "output_tokens": output, "prompt_ms": prompt_ms, "eval_ms": eval_ms}


def test_percentile_interpolates_like_numpy():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(values, 50) == pytest.approx(5.5)
    assert percentile(values, 95) == pytest.approx(9.55)
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None


def test_cold_start_is_reported_apart_from_steady_state():
    records = [rec(95_000, load_ms=4000, cached=0, prompt_ms=80_000)] + \
              [rec(ms) for ms in (6000, 7000, 5000, 8000)]
    report = summarize(records)
    cold = report["cold_start"]
    assert cold["total_s"] == 95 and cold["load_s"] == 4 and cold["cached_tokens"] == 0
    assert cold["prompt_tps"] == pytest.approx(1500 / 80)

    steady = report["steady"]
    assert steady["n"] == 4 and steady["total_s"] == 26
    assert steady["mean_s"] == 6.5 and steady["p50_s"] == 6.5 and steady["max_s"] == 8
    assert steady["p95_s"] == pytest.approx(7.85)
    assert steady["generation_tps"] == pytest.approx(120 / 12)
    assert steady["prompt_tps"] == pytest.approx(400 / 20)
    assert steady["cache_ratio"] == pytest.approx(5600 / 6000)


def test_empty_and_single_runs():
    assert summarize([]) == {"cold_start": None, "steady": summarize([])["steady"]}
    assert summarize([])["steady"]["n"] == 0 and summarize([])["steady"]["mean_s"] is None
    single = summarize([rec(1000)])
    assert single["cold_start"]["total_s"] == 1 and single["steady"]["n"] == 0
    assert single["steady"]["generation_tps"] is None


def test_record_from_result_sums_stages():
    result = ClassificationResult(status="done", stages=[
        StageRecord(name="lexicon"),
        StageRecord(name="classify", total_ms=6000, prompt_tokens=1500, cached_tokens=1400,
                    output_tokens=30, prompt_ms=3000, eval_ms=2800),
        StageRecord(name="verify", total_ms=4000, prompt_tokens=900, cached_tokens=850,
                    output_tokens=12, prompt_ms=2000, eval_ms=1500, load_ms=5),
    ])
    assert record_from_result(result) == {"total_ms": 10000, "load_ms": 5, "prompt_tokens": 2400,
                                          "cached_tokens": 2250, "output_tokens": 42,
                                          "prompt_ms": 5000, "eval_ms": 4300}


def test_hardware_spec_from_proc_files(tmp_path):
    cpu, mem = tmp_path / "cpuinfo", tmp_path / "meminfo"
    cpu.write_text(CPUINFO)
    mem.write_text(MEMINFO)
    spec = hardware_spec(cpu, mem)
    assert spec["model_name"] == "Intel(R) Celeron(R) CPU J4125 @ 2.00GHz"
    assert spec["logical_cpus"] == 3 and spec["physical_cores"] == 2
    assert spec["flags"] == {"sse4_2": True, "avx": False, "avx2": False, "fma": False}
    assert spec["mem_total_gib"] == 7.5


def test_hardware_spec_survives_missing_files(tmp_path):
    spec = hardware_spec(tmp_path / "none", tmp_path / "none")
    assert spec["model_name"] is None and spec["mem_total_gib"] is None


def test_cli(tmp_path):
    path = tmp_path / "timings.jsonl"
    path.write_text("\n".join(json.dumps(rec(ms)) for ms in (90_000, 6000, 7000)) + "\n")
    out = tmp_path / "perf.json"
    assert main(["--records", str(path), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["steady"]["n"] == 2 and "hardware" in report
