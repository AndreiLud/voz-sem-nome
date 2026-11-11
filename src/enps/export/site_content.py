"""Site sources: build settings, i18n strings, inline markup and number formatting.

Everything here is pure: the same inputs always give the same output, so the build is
deterministic and easy to test.
"""
from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from markupsafe import Markup

from enps import config

SITE_DIR = config.ROOT / "site"
SRC_DIR = SITE_DIR / "src"
PUBLIC_DIR = SITE_DIR / "public"

MINUS = "−"  # typographic minus for negative numbers
PLACEHOLDER_RE = re.compile(r"\[([A-Za-z]+)\]")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_FIELD_RE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class SiteSources:
    """Everything the build reads from site/src and config/."""
    site: dict[str, Any]
    strings: dict[str, dict[str, Any]]   # lang -> nested strings
    settings: dict[str, Any]
    taxonomy: dict[str, Any]

    @property
    def languages(self) -> list[str]:
        return list(self.site["languages"])

    @property
    def default_language(self) -> str:
        return str(self.site["default_language"])

    def page_path(self, page: str, lang: str) -> str:
        return str(self.site["pages"][page][lang])

    def other_language(self, lang: str) -> str:
        return next(code for code in self.languages if code != lang)


def read_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_sources(src: Path = SRC_DIR) -> SiteSources:
    site = read_yaml(src / "site.yaml")
    strings = {lang: read_yaml(src / "i18n" / f"{lang}.yaml") for lang in site["languages"]}
    return SiteSources(site=site, strings=strings, settings=config.settings(),
                       taxonomy=config.taxonomy())


def key_paths(tree: Any, prefix: str = "") -> set[str]:
    """Dotted paths of every leaf, with list items as [i]; used to compare languages."""
    if isinstance(tree, Mapping):
        out: set[str] = set()
        for key, value in tree.items():
            out |= key_paths(value, f"{prefix}.{key}" if prefix else str(key))
        return out
    if isinstance(tree, list):
        out = set()
        for i, value in enumerate(tree):
            out |= key_paths(value, f"{prefix}[{i}]")
        return out
    return {prefix}


def leaf_strings(tree: Any) -> list[str]:
    """Every string leaf of a nested structure, in document order."""
    if isinstance(tree, Mapping):
        return [s for value in tree.values() for s in leaf_strings(value)]
    if isinstance(tree, list):
        return [s for value in tree for s in leaf_strings(value)]
    return [tree] if isinstance(tree, str) else []


# ------------------------------------------------------------------- text helpers


def fill(template: str, **values: Any) -> str:
    """Replace {name} fields that have a value; unknown fields stay for the page scripts."""
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)
    return _FIELD_RE.sub(sub, template)


def inline(text: str, resolve_href: Any) -> Markup:
    """Escape `text`, then turn [label](href), **bold** and `code` into HTML.

    `resolve_href` maps "page:<id>[#anchor]" to a real URL. External links open in the
    same tab: the site never forces new windows.
    """
    escaped = html.escape(text, quote=False)

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1), html.unescape(match.group(2))
        url = resolve_href(href)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'

    out = _LINK_RE.sub(link, escaped)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    return Markup(out)


def chips(text: str, known: frozenset[str]) -> Markup:
    """Escape an anonymized text and render [PESSOA]-style placeholders as chips."""
    parts: list[str] = []
    last = 0
    for match in PLACEHOLDER_RE.finditer(text):
        if match.group(1) not in known:
            continue
        parts.append(html.escape(text[last:match.start()], quote=False))
        parts.append(chip(match.group(1)))
        last = match.end()
    parts.append(html.escape(text[last:], quote=False))
    return Markup("".join(parts))


def chip(code: str) -> str:
    """One placeholder chip. The brackets stay in the DOM for copy and paste."""
    name = html.escape(code)
    return (f'<span class="chip" data-placeholder="{name}">'
            f'<span class="chip__bracket">[</span>{name}<span class="chip__bracket">]</span></span>')


# ---------------------------------------------------------------- number format


def _group(digits: str, sep: str) -> str:
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return sep.join(out)


def fmt_number(value: float, lang: str, digits: int = 0) -> str:
    """Locale aware number: 1.234,5 in Portuguese, 1,234.5 in English."""
    thousands, decimal = (".", ",") if lang == "pt" else (",", ".")
    rounded = round(float(value), digits)
    sign = MINUS if rounded < 0 else ""
    whole, _, frac = f"{abs(rounded):.{digits}f}".partition(".")
    text = _group(whole, thousands)
    return sign + (f"{text}{decimal}{frac}" if digits else text)


def fmt_percent(share: float, lang: str, digits: int = 0) -> str:
    """0.873 -> 87% (digits=0) or 87,3% / 87.3% (digits=1)."""
    return f"{fmt_number(share * 100, lang, digits)}%"


def fmt_signed(value: float, lang: str) -> str:
    """eNPS style: +24, 0, -8 with a typographic minus."""
    text = fmt_number(value, lang)
    return f"+{text}" if round(value) > 0 else text


def fmt_hours(hours: float, lang: str) -> str:
    """Whole hours from 100 up, one decimal below. Same rule as the estimator script."""
    return fmt_number(hours, lang, 0 if abs(hours) >= 100 else 1)
