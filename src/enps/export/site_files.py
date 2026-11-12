"""Small generated files of the site: _headers, robots.txt, sitemap.xml and the data JSON."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from xml.sax.saxutils import escape

from enps.anonymize import PII_TYPES
from enps.export.site_content import SiteSources

FONT_CACHE = "public, max-age=31536000, immutable"
DATA_CACHE = "public, max-age=300, must-revalidate"
INDEXED_PAGES = ("home", "dashboard", "submit", "privacy")
EXTRA_PLACEHOLDERS = ("ofensivo",)  # profanity mask used in public excerpts
# Cloudflare Web Analytics: cookieless page view counts, injected by Cloudflare when turned on for the site.
ANALYTICS_SCRIPT_ORIGIN = "https://static.cloudflareinsights.com"
ANALYTICS_REPORT_ORIGIN = "https://cloudflareinsights.com"


def content_security_policy(turnstile_origin: str) -> str:
    """Self only, plus the Turnstile script and frame and the Cloudflare Web Analytics beacon."""
    directives = [
        "default-src 'self'",
        f"script-src 'self' {turnstile_origin} {ANALYTICS_SCRIPT_ORIGIN}",
        f"frame-src {turnstile_origin}",
        f"connect-src 'self' {ANALYTICS_REPORT_ORIGIN}",
        "img-src 'self'",
        "style-src 'self'",
        "font-src 'self'",
        "manifest-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "upgrade-insecure-requests",
    ]
    return "; ".join(directives)


def headers_file(turnstile_origin: str) -> str:
    """Cloudflare static assets _headers."""
    rules = {
        "/*": {
            "Content-Security-Policy": content_security_policy(turnstile_origin),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
            "X-Frame-Options": "DENY",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        },
        "/assets/fonts/*": {"Cache-Control": FONT_CACHE},
        "/data/*": {"Cache-Control": DATA_CACHE},
    }
    lines: list[str] = []
    for path, headers in rules.items():
        lines.append(path)
        lines.extend(f"  {name}: {value}" for name, value in headers.items())
    return "\n".join(lines) + "\n"


def robots_file(base_url: str) -> str:
    return f"User-agent: *\nDisallow: /api/\nAllow: /\n\nSitemap: {base_url}/sitemap.xml\n"


def sitemap_file(src: SiteSources, base_url: str) -> str:
    """Every indexed page in both languages, with hreflang alternates."""
    html_lang = src.site["html_lang"]
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:xhtml="http://www.w3.org/1999/xhtml">']
    for page in INDEXED_PAGES:
        for lang in src.languages:
            rows.append("  <url>")
            rows.append(f"    <loc>{escape(base_url + src.page_path(page, lang))}</loc>")
            for alt in src.languages:
                href = escape(base_url + src.page_path(page, alt))
                rows.append(f'    <xhtml:link rel="alternate" hreflang="{html_lang[alt]}" href="{href}"/>')
            default = escape(base_url + src.page_path(page, src.default_language))
            rows.append(f'    <xhtml:link rel="alternate" hreflang="x-default" href="{default}"/>')
            rows.append("  </url>")
    rows.append("</urlset>")
    return "\n".join(rows) + "\n"


def _labelled(items: Iterable[Mapping[str, Any]], with_definition: bool) -> list[dict[str, Any]]:
    out = []
    for item in items:
        entry = {"code": item["code"], "label": dict(item["label"])}
        if with_definition and "definition" in item:
            entry["definition"] = dict(item["definition"])
        out.append(entry)
    return out


def taxonomy_json(src: SiteSources) -> dict[str, Any]:
    """Labels in both languages for every code the dashboard and the form show."""
    tax, survey = src.taxonomy, src.settings["survey"]
    placeholders = [*PII_TYPES, *EXTRA_PLACEHOLDERS]
    return {
        "version": tax.get("version"),
        "themes": _labelled(tax["themes"], True),
        "sentiments": _labelled(tax["sentiments"], True),
        "critical_types": _labelled(tax["critical_types"], True),
        "areas": _labelled(survey["areas"], False),
        "tenure_bands": _labelled(survey["tenure_bands"], False),
        "placeholders": [
            {"code": code, "label": {lang: src.strings[lang]["placeholders"][code] for lang in src.languages}}
            for code in placeholders
        ],
        "question": dict(survey["question"]),
    }


def config_json(src: SiteSources, *, turnstile_key: str, estimator: Mapping[str, Any],
                machine_s: float | None, samples: Mapping[str, bool]) -> dict[str, Any]:
    """Runtime settings read by the page scripts."""
    site, privacy = src.site, src.settings["privacy"]
    return {
        "api_base": site["api_base"],
        "languages": src.languages,
        "default_language": src.default_language,
        "min_group_size": privacy["min_group_size"],
        "max_comment_chars": privacy["max_comment_chars"],
        "public_retention_days": privacy["public_retention_days"],
        "duplicate_window_hours": src.settings["limits"]["duplicate_window_hours"],
        "poll_interval_ms": site["poll_interval_ms"],
        "turnstile_site_key": turnstile_key,
        "routes": {page: dict(paths) for page, paths in site["pages"].items()},
        "anchors": {key: dict(ids) for key, ids in site["anchors"].items()},
        "query": site["query"],
        "data": {
            "dashboard_synthetic": "/data/dashboard-synthetic.json",
            "taxonomy": "/data/taxonomy.json",
        },
        "estimator": {"defaults": dict(estimator), "machine_seconds_per_comment": machine_s},
        "sample": dict(samples),
    }


def dump_json(data: Any) -> str:
    """Stable, readable JSON (UTF-8, two space indent, trailing newline)."""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
