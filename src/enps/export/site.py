"""Static site build: site/src (templates, strings, static files) -> site/public.

    python -m enps.export.site [--out DIR] [--metrics PATH] [--dashboard PATH]

Pages are rendered in Portuguese (root) and English (/en/). Data inputs:
metrics/metrics.json and site/public/data/dashboard-synthetic.json (exported by the
pipeline). Each falls back to a fixture under site/src/fixtures/ marked as sample data.
The output depends only on the inputs, so two builds of the same sources are identical.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from enps.anonymize import PII_TYPES
from enps.export import site_files
from enps.export.site_content import (
    PUBLIC_DIR, SRC_DIR, SiteSources, chips, fill, fmt_hours, fmt_number, fmt_percent,
    fmt_signed, inline, load_sources,
)
from enps.export.site_data import (
    DASHBOARD_SYNTHETIC_PATH, ESTIMATOR_FIELDS, METRICS_PATH, MODES, Loaded, ResultValue,
    comparison, dashboard_preview, estimate, hardware, load_with_fallback, machine_time,
    mode_blocks, pipeline, reference_kind, result_values,
)

PAGES = ("home", "dashboard", "submit", "privacy", "not_found")
PAGE_SCRIPTS = {"home": "home", "dashboard": "dashboard", "submit": "submit"}
PAGE_STRINGS = {"dashboard": "dashboard", "submit": "submit"}  # i18n sections sent to scripts
CHIP_CODES = frozenset([*PII_TYPES, *site_files.EXTRA_PLACEHOLDERS])
KEEP_ON_CLEAN = ("assets/og",)  # generated outside this build (social cards)
TURNSTILE_ENV = "TURNSTILE_SITE_KEY"
TIME_TRAVEL_DAYS = 7  # D1 point-in-time recovery window on the Workers Free plan


@dataclass
class BuildReport:
    out: Path
    pages: list[str] = field(default_factory=list)
    samples: dict[str, bool] = field(default_factory=dict)
    turnstile_test_key: bool = False


# ------------------------------------------------------------------ paths


def output_file(url_path: str) -> str:
    """"/" -> index.html, "/painel/" -> painel/index.html, "/404.html" -> 404.html."""
    rel = url_path.lstrip("/")
    return rel + "index.html" if rel == "" or rel.endswith("/") else rel


def _json_for_script(data: Any) -> Markup:
    """JSON safe to embed in a <script type="application/json"> block."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(char, escaped)
    return Markup(text)


# --------------------------------------------------------------- renderer


class SiteRenderer:
    """Renders every page of one language with the shared helpers."""

    def __init__(self, src: SiteSources, src_dir: Path, *, metrics: Loaded,
                 dashboard: Loaded, turnstile_key: str) -> None:
        self.src = src
        self.metrics = metrics
        self.dashboard = dashboard
        self.turnstile_key = turnstile_key
        self.base_url = str(src.settings["site"]["base_url"]).rstrip("/")
        self.env = Environment(
            loader=FileSystemLoader(src_dir / "templates"),
            autoescape=True, undefined=StrictUndefined,
            trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        )

    # helpers bound to one language -------------------------------------

    def url(self, lang: str, page: str, anchor: str | None = None) -> str:
        path = self.src.page_path(page, lang)
        return f"{path}#{self.anchor(lang, anchor)}" if anchor else path

    def anchor(self, lang: str, key: str) -> str:
        return str(self.src.site["anchors"][key][lang])

    def resolve_href(self, lang: str, href: str) -> str:
        """page:<id>[#<anchor key>] -> localized URL; anything else unchanged."""
        if not href.startswith("page:"):
            return href
        page, _, anchor = href[5:].partition("#")
        return self.url(lang, page, anchor or None)

    def text_vars(self) -> dict[str, Any]:
        privacy, llm, models = self.src.settings["privacy"], self.src.settings["llm"], self.src.site["models"]
        return {
            "k": privacy["min_group_size"],
            "days": privacy["public_retention_days"],
            "alert_days": privacy["alert_log_retention_days"],
            # deleted D1 rows stay restorable by Time Travel for 7 days (Workers Free)
            "gone_days": privacy["public_retention_days"] + TIME_TRAVEL_DAYS,
            "max": privacy["max_comment_chars"],
            "model": llm["model"],
            "edge_model": models["edge"]["name"],
            "edge_model_id": models["edge"]["id"],
            "local_model": models["local"]["name"],
        }

    # page data ----------------------------------------------------------

    def results(self, lang: str) -> list[dict[str, Any]]:
        """Headline tiles (live demo) with values already formatted for `lang`."""
        return [self._formatted(item, lang) for item in result_values(self.metrics.data, "edge")]

    def comparison(self, lang: str) -> list[dict[str, Any]]:
        """Rows of the live demo vs local mode table, formatted for `lang`."""
        return [{"key": row["edge"].key, **{mode: self._formatted(row[mode], lang) for mode in MODES}}
                for row in comparison(self.metrics.data)]

    def _formatted(self, item: ResultValue, lang: str) -> dict[str, Any]:
        return {
            "key": item.key,
            "measured": item.measured,
            "value": self._fmt_kind(item.kind, item.value, lang),
            "n": fmt_number(item.n, lang) if item.n is not None else None,
            "ci": ({"lo": self._fmt_kind(item.kind, item.ci[0], lang),
                    "hi": self._fmt_kind(item.kind, item.ci[1], lang)} if item.ci else None),
            "extra": self._fmt_kind(item.extra_kind, item.extra, lang),
        }

    @staticmethod
    def _fmt_kind(kind: str, value: float | None, lang: str) -> str | None:
        if value is None:
            return None
        if kind == "share":
            return fmt_percent(value, lang)
        if kind == "seconds":
            return f"{fmt_number(value, lang, 1)} s"
        return fmt_number(value, lang, 2)

    def estimator(self, lang: str) -> dict[str, Any]:
        defaults = self.src.site["estimator"]
        machine = machine_time(self.metrics.data)
        machine_s = machine.seconds if machine else None
        result = estimate(defaults, machine_s)
        rows = []
        for key, kind, low, high, step in ESTIMATOR_FIELDS:
            value = defaults[key] * 100 if kind == "share" else defaults[key]
            digits = 0 if float(value).is_integer() else 1
            rows.append({"key": key, "kind": kind, "min": low, "max": high, "step": step,
                         "value": fmt_number(value, lang, digits)})
        return {
            "rows": rows,
            "comments": fmt_number(result.comments, lang),
            "manual": fmt_hours(result.manual_hours, lang),
            "review": fmt_hours(result.review_hours, lang),
            "saved": fmt_hours(result.saved_hours, lang),
            "machine": fmt_hours(result.machine_hours, lang) if result.machine_hours is not None else None,
            "defaults": dict(defaults),
            "machine_s": machine_s,
            "machine_mode": machine.mode if machine else None,
        }

    def preview(self, lang: str) -> dict[str, Any]:
        view = dashboard_preview(self.dashboard.data)
        if view.enps is None:
            return {"empty": True}
        return {
            "empty": False,
            "enps": fmt_signed(view.enps, lang),
            "responses": fmt_number(view.responses or 0, lang),
            "segments": [
                {**seg, "count_text": fmt_number(seg["count"], lang),
                 "share_text": fmt_percent(seg["share"], lang),
                 "width": round(seg["share"] * 100, 3)}
                for seg in view.segments
            ],
            "offsets": _offsets([seg["share"] for seg in view.segments]),
        }

    def page_data(self, lang: str, page: str) -> dict[str, Any]:
        strings = dict(self.src.strings[lang]["js"])
        section = PAGE_STRINGS.get(page)
        if section:
            strings[section] = self.src.strings[lang][section]
        data: dict[str, Any] = {
            "lang": lang,
            "page": page,
            "strings": strings,
            "routes": self.src.site["pages"],
            "anchors": self.src.site["anchors"],
            "query": self.src.site["query"],
            "config": "/data/config.json",
            "taxonomy": "/data/taxonomy.json",
            "vars": self.text_vars(),
        }
        if page == "home":
            est = self.estimator(lang)
            data["estimator"] = {"defaults": est["defaults"], "machine_s": est["machine_s"]}
            data["strings"]["estimate"] = self.src.strings[lang]["home"]["estimate"]
        return data

    # rendering ----------------------------------------------------------

    def context(self, lang: str, page: str) -> dict[str, Any]:
        src, s = self.src, self.src.strings[lang]
        other = src.other_language(lang)
        alt_page = "home" if page == "not_found" else page
        vars_ = self.text_vars()
        alternates = [{"hreflang": src.site["html_lang"][code],
                       "href": self.base_url + src.page_path(page, code)} for code in src.languages]
        alternates.append({"hreflang": "x-default",
                           "href": self.base_url + src.page_path(page, src.default_language)})

        def md(text: str, **values: Any) -> Markup:
            return inline(fill(text, **{**vars_, **values}), lambda h: self.resolve_href(lang, h))

        return {
            "lang": lang, "page": page, "s": s, "site": src.site, "settings": src.settings,
            "html_lang": src.site["html_lang"][lang],
            "other_lang": other, "other_html_lang": src.site["html_lang"][other],
            "alt_url": self.url(other, alt_page),
            "canonical": self.base_url + src.page_path(page, lang),
            "alternates": alternates,
            "og_image": f"{self.base_url}/assets/og/og-{lang}.png",
            "og_locale": src.site["og_locale"][lang], "og_locale_alt": src.site["og_locale"][other],
            "meta": s["meta"][page],
            "noindex": page == "not_found",
            "script": PAGE_SCRIPTS.get(page),
            "page_data": _json_for_script(self.page_data(lang, page)),
            "turnstile_key": self.turnstile_key,
            "turnstile_script": src.site["turnstile_script"],
            "url": lambda p, a=None: self.url(lang, p, a),
            "anchor": lambda key: self.anchor(lang, key),
            "md": md,
            "fill": lambda text, **values: fill(text, **{**vars_, **values}),
            "chips": lambda text: chips(text, CHIP_CODES),
            "vars": vars_,
            "samples": {"metrics": self.metrics.sample, "dashboard": self.dashboard.sample},
            "results": self.results(lang) if page == "home" else [],
            "compare": self.comparison(lang) if page == "home" else [],
            "result_notes": self._result_notes(lang) if page == "home" else {},
            "est": self.estimator(lang) if page == "home" else {},
            "preview": self.preview(lang) if page == "home" else {},
            "placeholder_codes": [*PII_TYPES, *site_files.EXTRA_PLACEHOLDERS],
            "areas": src.settings["survey"]["areas"],
            "tenures": src.settings["survey"]["tenure_bands"],
            "question": src.settings["survey"]["question"][lang],
        }

    def _result_notes(self, lang: str) -> dict[str, str | None]:
        """Reference labels, versions of each mode and the local benchmark machine."""
        r = self.src.strings[lang]["home"]["results"]
        blocks = mode_blocks(self.metrics.data)
        refs = [ref for ref in (reference_kind(blocks[mode]) for mode in MODES) if ref]
        hw = hardware(self.metrics.data)
        notes: dict[str, str | None] = {
            "reference": r["reference"][refs[0]] if refs else None,
            "hardware": fill(r["hardware"], cpus=hw["cpus"], mem=fmt_number(hw["mem"], lang, 1)) if hw else None,
        }
        for mode in MODES:
            parts = [fill(r["pipeline_parts"][name], value=value) for name, value in pipeline(blocks[mode]).items()]
            notes[f"pipeline_{mode}"] = fill(r["pipeline"][mode], parts=", ".join(parts)) if parts else None
        return notes

    def render(self, lang: str, page: str) -> str:
        template = self.env.get_template(f"{page}.html.j2")
        return template.render(**self.context(lang, page))


def _offsets(shares: list[float]) -> list[float]:
    """Start position (percent) of each segment of a 100% bar."""
    out, total = [], 0.0
    for share in shares:
        out.append(round(total * 100, 3))
        total += share
    return out


# ------------------------------------------------------------------ build steps


def _clean(out: Path, *, force: bool) -> None:
    """Empty the output folder, keeping files produced by other tools."""
    if out.exists() and any(out.iterdir()) and not force and out.name != "public":
        raise SystemExit(f"refusing to clean {out}: not a 'public' folder (use --force)")
    out.mkdir(parents=True, exist_ok=True)
    keep = {out / rel for rel in KEEP_ON_CLEAN}
    for child in sorted(out.iterdir()):
        if child == out / "assets":
            for sub in sorted(child.iterdir()):
                if sub not in keep:
                    _remove(sub)
        else:
            _remove(child)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_static(src_dir: Path, out: Path) -> None:
    """static/ -> assets/, with every css/*.css joined (in name order) into one site.css."""
    static, assets = src_dir / "static", out / "assets"
    for path in sorted(static.rglob("*")):
        rel = path.relative_to(static)
        if path.is_dir() or rel.parts[0] == "css":
            continue
        target = assets / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    css_parts = [p.read_text(encoding="utf-8") for p in sorted((static / "css").glob("*.css"))]
    css = assets / "css" / "site.css"
    css.parent.mkdir(parents=True, exist_ok=True)
    css.write_text("\n".join(css_parts), encoding="utf-8")


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def _turnstile_key(src: SiteSources, environ: Mapping[str, str]) -> tuple[str, bool]:
    key = environ.get(TURNSTILE_ENV, "").strip() or str(src.site.get("turnstile_site_key") or "").strip()
    return (key, False) if key else (str(src.site["turnstile_test_key"]), True)


def build(
    out: Path = PUBLIC_DIR,
    *,
    src_dir: Path = SRC_DIR,
    metrics_path: Path = METRICS_PATH,
    dashboard_path: Path = DASHBOARD_SYNTHETIC_PATH,
    environ: Mapping[str, str] | None = None,
    force: bool = False,
) -> BuildReport:
    """Render the whole site into `out` and return what was used."""
    environ = os.environ if environ is None else environ
    src = load_sources(src_dir)
    metrics = load_with_fallback(metrics_path, src_dir / "fixtures" / "metrics.sample.json")
    dashboard = load_with_fallback(dashboard_path, src_dir / "fixtures" / "dashboard-synthetic.json")
    turnstile_key, test_key = _turnstile_key(src, environ)
    report = BuildReport(out=out, samples={"metrics": metrics.sample, "dashboard_synthetic": dashboard.sample},
                         turnstile_test_key=test_key)

    _clean(out, force=force)
    _copy_static(src_dir, out)

    renderer = SiteRenderer(src, src_dir, metrics=metrics, dashboard=dashboard,
                            turnstile_key=turnstile_key)
    for lang in src.languages:
        for page in PAGES:
            rel = output_file(src.page_path(page, lang))
            _write(out / rel, renderer.render(lang, page))
            report.pages.append(rel)

    data = out / "data"
    machine = machine_time(metrics.data)
    _write(data / "dashboard-synthetic.json", dashboard.raw)
    _write(data / "taxonomy.json", site_files.dump_json(site_files.taxonomy_json(src)))
    _write(data / "config.json", site_files.dump_json(site_files.config_json(
        src, turnstile_key=turnstile_key, estimator=src.site["estimator"],
        machine_s=machine.seconds if machine else None, samples=report.samples)))

    base_url = renderer.base_url
    turnstile_origin = "https://" + src.site["turnstile_script"].split("/")[2]
    _write(out / "_headers", site_files.headers_file(turnstile_origin))
    _write(out / "robots.txt", site_files.robots_file(base_url))
    _write(out / "sitemap.xml", site_files.sitemap_file(src, base_url))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static site into site/public.")
    parser.add_argument("--out", type=Path, default=PUBLIC_DIR)
    parser.add_argument("--metrics", type=Path, default=METRICS_PATH)
    parser.add_argument("--dashboard", type=Path, default=DASHBOARD_SYNTHETIC_PATH,
                        help="dashboard JSON of the synthetic dataset (pipeline export)")
    parser.add_argument("--force", action="store_true", help="allow cleaning an --out not named public")
    parser.add_argument("--release", action="store_true",
                        help="fail when any page would use sample data or the Turnstile test key")
    args = parser.parse_args(argv)
    report = build(args.out, metrics_path=args.metrics, dashboard_path=args.dashboard, force=args.force)
    print(f"built {len(report.pages)} pages into {report.out}")
    problems = [f"{name} uses sample fixture data" for name, sample in report.samples.items() if sample]
    if report.turnstile_test_key:
        problems.append(f"{TURNSTILE_ENV} not set, using the Turnstile test key")
    for problem in problems:
        print(f"{'error' if args.release else 'warning'}: {problem}", file=sys.stderr)
    return 1 if args.release and problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
