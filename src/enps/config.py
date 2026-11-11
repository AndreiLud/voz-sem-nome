"""Settings and taxonomy loading.

settings.yaml holds defaults; ENPS_<SECTION>_<KEY> environment variables override them.
The taxonomy is the single source of truth for theme, sentiment and critical-type codes.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("ENPS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"


def _coerce(raw: str, current: Any) -> Any:
    """Convert an environment string to the type of the default value."""
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, (list, dict)):
        return json.loads(raw)
    return raw


def _apply_env(settings: dict[str, Any], environ: dict[str, str]) -> dict[str, Any]:
    for section, values in settings.items():
        if not isinstance(values, dict):
            continue
        for key, current in values.items():
            name = f"ENPS_{section}_{key}".upper()
            if name in environ:
                values[key] = _coerce(environ[name], current)
    return settings


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return _apply_env(data, dict(os.environ))


@lru_cache(maxsize=1)
def taxonomy() -> dict[str, Any]:
    with open(CONFIG_DIR / "taxonomy.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def theme_codes() -> list[str]:
    return [t["code"] for t in taxonomy()["themes"]]


def sentiment_codes() -> list[str]:
    return [s["code"] for s in taxonomy()["sentiments"]]


def critical_codes() -> list[str]:
    return [c["code"] for c in taxonomy()["critical_types"]]


def area_codes() -> list[str]:
    return [a["code"] for a in settings()["survey"]["areas"]]


def tenure_codes() -> list[str]:
    return [t["code"] for t in settings()["survey"]["tenure_bands"]]


def resolve(path: str | Path) -> Path:
    """Resolve a settings path relative to the repository root."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
