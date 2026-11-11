"""Versioned prompt files and their rendering.

A prompt file (prompts/<name>.md) has YAML front matter (version, task, description) followed by
the system prompt template. Placeholders {{themes}}, {{sentiments}} and {{critical_types}} are
filled from config/taxonomy.yaml, so the model always reads the same definitions as the labelers.

The stored prompt version is "<name>+<first 8 hex of sha256(rendered system prompt)>": editing the
file or the taxonomy changes it, and every classification records which text produced it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from enps import config

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_REQUIRED_KEYS = ("version", "task", "description")
_QUOTE_RUN = re.compile(r'"{3,}')


@dataclass(frozen=True)
class Prompt:
    name: str            # file stem, e.g. "classify.v1"
    task: str            # cache folder and stage name, e.g. "classify"
    version: int
    description: str
    system: str          # rendered system prompt sent to the model

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.system.encode("utf-8")).hexdigest()[:8]

    @property
    def prompt_version(self) -> str:
        return f"{self.name}+{self.digest}"


def render_themes(taxonomy: dict[str, Any]) -> str:
    return "\n".join(f"- {t['code']}: {t['label']['pt']}. {t['definition']['pt']}"
                     for t in taxonomy["themes"])


def render_sentiments(taxonomy: dict[str, Any]) -> str:
    return "\n".join(f"- {s['code']}: {s['definition']['pt']}" for s in taxonomy["sentiments"])


def render_critical_types(taxonomy: dict[str, Any]) -> str:
    return "\n".join(
        f"- {c['code']}: {c['label']['pt']}. {c['definition']['pt']} Sinais: {c['signals']['pt']}"
        for c in taxonomy["critical_types"])


RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "themes": render_themes,
    "sentiments": render_sentiments,
    "critical_types": render_critical_types,
}


def split_front_matter(source: str) -> tuple[dict[str, Any], str]:
    match = _FRONT_MATTER.match(source)
    if not match:
        raise ValueError("prompt file must start with YAML front matter between --- lines")
    meta = yaml.safe_load(match.group(1)) or {}
    missing = [k for k in _REQUIRED_KEYS if k not in meta]
    if missing:
        raise ValueError(f"prompt front matter is missing {missing}")
    return meta, source[match.end():]


def render(template: str, taxonomy: dict[str, Any]) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in RENDERERS:
            raise ValueError(f"unknown prompt placeholder {{{{{name}}}}}")
        return RENDERERS[name](taxonomy)

    return _PLACEHOLDER.sub(fill, template).strip() + "\n"


def parse_prompt(name: str, source: str, taxonomy: dict[str, Any]) -> Prompt:
    meta, template = split_front_matter(source)
    return Prompt(name=name, task=str(meta["task"]), version=int(meta["version"]),
                  description=str(meta["description"]), system=render(template, taxonomy))


@lru_cache(maxsize=None)
def load_prompt(name: str, prompts_dir: Path | None = None) -> Prompt:
    """Load prompts/<name>.md rendered with the current taxonomy."""
    path = (prompts_dir or config.PROMPTS_DIR) / f"{name}.md"
    return parse_prompt(name, path.read_text(encoding="utf-8"), config.taxonomy())


def user_message(text: str, error_hint: str | None = None) -> str:
    """The user turn: only the comment, plus the validation error on a retry."""
    body = _QUOTE_RUN.sub('"', text.strip())  # the comment can never close the delimiter
    message = f'Comentário: """{body}"""'
    if error_hint:
        message += (f"\n\nSua resposta anterior foi recusada: {error_hint}. "
                    "Responda de novo, só com o JSON, seguindo as regras.")
    return message
