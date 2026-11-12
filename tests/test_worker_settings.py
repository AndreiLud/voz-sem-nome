import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("build_worker_settings", ROOT / "scripts" / "build_worker_settings.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_worker_settings"] = module
    spec.loader.exec_module(module)
    return module


build = _load()


def test_worker_settings_module_is_current():
    assert build.OUTPUT.read_text(encoding="utf-8") == build.render(), \
        "worker/settings.js is stale: run scripts/build_worker_settings.py"
    assert build.main(["--check"]) == 0


def test_worker_settings_keep_the_yaml_names_and_values():
    cfg = build.load_settings()
    subset = build.worker_settings(cfg)
    assert subset["limits"] == cfg["limits"]
    assert subset["privacy"] == cfg["privacy"]
    assert subset["queue"] == {"max_attempts": cfg["queue"]["max_attempts"], "eta_window": cfg["queue"]["eta_window"]}
    assert [a["code"] for a in subset["survey"]["areas"]] == [a["code"] for a in cfg["survey"]["areas"]]
    assert subset["survey"]["tenure_bands"] == [t["code"] for t in cfg["survey"]["tenure_bands"]]
    assert subset["site"]["dashboard_path"]["pt"] == cfg["site"]["dashboard_path"]["pt"]
    assert "llm" not in subset and "paths" not in subset


def test_check_fails_when_the_yaml_changes():
    cfg = copy.deepcopy(build.load_settings())
    cfg["limits"]["global_hour_max"] += 1
    assert build.render(cfg) != build.OUTPUT.read_text(encoding="utf-8")
