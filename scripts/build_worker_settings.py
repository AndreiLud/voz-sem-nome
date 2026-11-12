"""Write worker/settings.js, the part of config/settings.yaml the Cloudflare Worker uses.

The Worker cannot read YAML, so the sections it needs (survey codes and area labels, privacy,
queue, limits, alerts, site) are written to a JavaScript module here, keeping the YAML names.
tests/test_worker_settings.py fails when the module is stale.

    .venv/bin/python scripts/build_worker_settings.py            # write the module
    .venv/bin/python scripts/build_worker_settings.py --check    # exit 1 when it is stale

Settings are read from config/settings.yaml without ENPS_* environment overrides, so the output
only depends on the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = ROOT / "config" / "settings.yaml"
OUTPUT = ROOT / "worker" / "settings.js"

HEADER = """\
// Settings of the Worker: a copy of the sections of config/settings.yaml it uses, with the same
// names. Do not edit: this file is written by scripts/build_worker_settings.py, and
// tests/test_worker_settings.py fails when it is stale.
"""

FOOTER = """
function deepFreeze(value) {
  if (value && typeof value === "object") {
    for (const item of Object.values(value)) deepFreeze(item);
    Object.freeze(value);
  }
  return value;
}
"""


def load_settings(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def worker_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """The subset of the settings the Worker reads."""
    survey = cfg["survey"]
    return {
        "survey": {
            "areas": [{"code": a["code"], "label": {"pt": a["label"]["pt"]}} for a in survey["areas"]],
            "tenure_bands": [t["code"] for t in survey["tenure_bands"]],
        },
        "privacy": dict(cfg["privacy"]),
        "queue": {key: cfg["queue"][key] for key in ("max_attempts", "eta_window")},
        "limits": dict(cfg["limits"]),
        "alerts": {key: cfg["alerts"][key] for key in ("datasets", "channels", "immediate_per_hour")},
        "site": {"base_url": cfg["site"]["base_url"], "dashboard_path": dict(cfg["site"]["dashboard_path"])},
    }


def render(cfg: dict[str, Any] | None = None) -> str:
    data = worker_settings(cfg if cfg is not None else load_settings())
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return f"{HEADER}\nexport const SETTINGS = deepFreeze({body});\n{FOOTER}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when worker/settings.js is stale")
    args = parser.parse_args(argv)
    text = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
    if args.check:
        if current != text:
            print(f"{OUTPUT.relative_to(ROOT)} is stale: run scripts/build_worker_settings.py", file=sys.stderr)
            return 1
        return 0
    if current != text:
        OUTPUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
