"""Submit page, its scripts' strings and slots, the Cloudflare Worker and wrangler.jsonc."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest

from enps import config
from enps.export import site
from enps.export.site_content import SRC_DIR, load_sources

ROOT = config.ROOT
JS_DIR = SRC_DIR / "static" / "js"
SUBMIT_JS = [JS_DIR / "submit.js", *sorted((JS_DIR / "submit").glob("*.js"))]
WORKER_DIR = ROOT / "worker"


# ------------------------------------------------------------------ helpers


class Elements(HTMLParser):
    """Every start tag with its attributes, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {k: v or "" for k, v in attrs}))

    def find(self, **wanted: str) -> list[dict[str, str]]:
        return [a for _, a in self.tags if all(a.get(k.replace("_", "-")) == v for k, v in wanted.items())]


def strip_jsonc(text: str) -> str:
    """JSONC -> JSON: drops // comments outside strings (the file has no block comments)."""
    out, i, in_string = [], 0, False
    while i < len(text):
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(text[i + 1])
                i += 2
                continue
            in_string = char != '"'
        elif char == '"':
            in_string = True
            out.append(char)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(char)
        i += 1
    return "".join(out)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("submit") / "public"
    missing = out.parent / "missing.json"
    site.build(out, metrics_path=missing, dashboard_path=missing, environ={})
    return out


@pytest.fixture(scope="module")
def sources():
    return load_sources(SRC_DIR)


@pytest.fixture(scope="module", params=["pt", "en"])
def submit_page(request, built, sources) -> tuple[str, Elements, str]:
    lang = request.param
    html = (built / site.output_file(sources.page_path("submit", lang))).read_text(encoding="utf-8")
    parsed = Elements()
    parsed.feed(html)
    return lang, parsed, html


@pytest.fixture(scope="module")
def wrangler() -> dict:
    return json.loads(strip_jsonc((ROOT / "wrangler.jsonc").read_text(encoding="utf-8")))


def _js_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in SUBMIT_JS)


# ------------------------------------------------------------------ page markup


def test_form_fields_follow_the_contract(submit_page):
    _, page, _ = submit_page
    settings = config.settings()
    scores = [a["value"] for a in page.find(name="score")]
    assert scores == [str(i) for i in range(11)]
    areas = [a["value"] for tag, a in page.tags if tag == "option" and a.get("value")]
    assert areas == config.area_codes()
    assert [a["value"] for a in page.find(name="tenure")] == config.tenure_codes()
    (comment,) = page.find(id="comment")
    assert comment["data-max"] == str(settings["privacy"]["max_comment_chars"])
    assert "maxlength" not in comment  # the counter and the error handle the limit, nothing is cut
    assert page.find(id="consent")[0]["type"] == "checkbox"
    (form,) = page.find(id="submit-form")
    assert "novalidate" in form and form["method"] == "post"


def test_errors_are_linked_only_through_data_error(submit_page):
    _, page, _ = submit_page
    ids = {a["id"] for _, a in page.tags if "id" in a}
    controls = [a for _, a in page.tags if "data-error" in a]
    assert len(controls) == 11 + 1 + 4 + 1 + 1 + 1  # score, area, tenure, comment, consent, lookup code
    for attrs in controls:
        assert attrs["data-error"] in ids, attrs
        assert attrs["data-error"] not in attrs.get("aria-describedby", ""), attrs
    errors = [a for _, a in page.tags if "field-error" in a.get("class", "").split()]
    assert errors and all("hidden" in a for a in errors)


def test_turnstile_widget(submit_page, sources):
    lang, page, html = submit_page
    (widget,) = [a for _, a in page.tags if "cf-turnstile" in a.get("class", "").split()]
    assert widget["data-sitekey"] == sources.site["turnstile_site_key"]  # the real public key ships
    assert widget["data-response-field-name"] == "turnstile"
    assert widget["data-language"] == ("pt-br" if lang == "pt" else "en")
    assert sources.site["turnstile_script"] in html


def test_live_feedback_and_progress(submit_page):
    _, page, _ = submit_page
    (feedback,) = page.find(data_slot="submit-feedback")
    assert feedback["role"] == "alert"
    (progress,) = page.find(data_slot="progress")
    assert progress["max"] == "3" and progress.get("aria-label")
    (title,) = page.find(id="tracking-title")
    assert title["tabindex"] == "-1"
    (summary,) = page.find(data_slot="error-summary")
    assert summary["tabindex"] == "-1" and "hidden" in summary


def test_every_slot_used_by_the_scripts_exists(submit_page):
    _, page, _ = submit_page
    source = _js_source()
    used = set(re.findall(r'slot\("([a-z-]+)"\)', source))
    used |= set(re.findall(r'\b\w+: "([a-z]+(?:-[a-z]+)*)"', source.split("function slots()")[1].split("const ui")[0]))
    present = {a["data-slot"] for _, a in page.tags if "data-slot" in a}
    assert used and used <= present, sorted(used - present)


# ------------------------------------------------------------------ strings


def _string_keys_used() -> set[str]:
    source = _js_source()
    keys = set(re.findall(r'\bt\(\s*"(submit\.[a-z_.]+)"', source))
    rules = (JS_DIR / "submit" / "rules.js").read_text(encoding="utf-8")
    keys |= {f"submit.api_errors.{k}" for k in re.findall(r'key: "([a-z_]+)"', rules)}
    validate_body = rules.split("export function validate")[1].split("export function")[0]
    keys |= {f"submit.errors.{k}" for k in re.findall(r'"([a-z_]+)"\)', validate_body)}
    keys |= {f"submit.errors.{k}" for k in re.findall(r': "([a-z_]+)"', rules.split("fieldErrorsFromApi")[1].split("const errors")[0])}
    stages = re.search(r"STAGES = \{([^}]+)\}", rules).group(1)
    keys |= {f"submit.track.status.{k}" for k in re.findall(r"(\w+):", stages)}
    keys |= {"submit.track.offline", "submit.track.reconnecting", "submit.submit", "submit.sending"}  # built from a variable
    return keys


def test_every_string_the_scripts_use_exists_in_both_languages(sources):
    keys = _string_keys_used()
    assert len(keys) > 40
    for lang in sources.languages:
        for key in sorted(keys):
            node = sources.strings[lang]
            for part in key.split("."):
                assert isinstance(node, dict) and part in node, (lang, key)
                node = node[part]
            assert isinstance(node, str) and node.strip(), (lang, key)


def test_support_line_for_burnout(sources):
    pt = sources.strings["pt"]["submit"]["result"]["cvv"]
    en = sources.strings["en"]["submit"]["result"]["cvv"]
    assert "188" in pt and "cvv.org.br" in pt and "24 horas" in pt
    assert "188" in en and "Brazilian" in en


# ------------------------------------------------------------------ Worker and wrangler.jsonc


def test_wrangler_config(wrangler):
    assert wrangler["name"] == "voz-sem-nome"
    assert (ROOT / wrangler["main"]).is_file()
    assert wrangler["compatibility_date"] == "2026-11-10"
    assert wrangler["assets"] == {
        "directory": "./site/public", "binding": "ASSETS", "run_worker_first": ["/api/*"],
        "html_handling": "auto-trailing-slash", "not_found_handling": "404-page",
    }
    assert wrangler["routes"] == [{"pattern": "voz-sem-nome.andreiludescher.com", "custom_domain": True}]
    assert wrangler["workers_dev"] is False and wrangler["preview_urls"] is False
    # Invocation logs would record request details; the Worker logs only event names and codes.
    assert wrangler["observability"] == {"enabled": True, "logs": {"invocation_logs": False}}
    assert wrangler["ai"] == {"binding": "AI"}
    assert [(d["binding"], d["database_name"]) for d in wrangler["d1_databases"]] == [("DB", "voz-sem-nome")]
    assert set(wrangler["vars"]) == {"AI_MODEL", "PUBLIC_START_DAY"}
    assert wrangler["vars"]["AI_MODEL"] == "@cf/google/gemma-4-26b-a4b-it"
    # A real Sao Paulo day, or empty to count every answer (worker/reads.js publicStart).
    start = wrangler["vars"]["PUBLIC_START_DAY"]
    assert start == "" or date.fromisoformat(start).isoformat() == start
    assert "kv_namespaces" not in wrangler
    assert wrangler["triggers"] == {"crons": ["*/10 * * * *", "17 6 * * *"]}


def test_wrangler_documents_secrets_without_values(wrangler):
    text = (ROOT / "wrangler.jsonc").read_text(encoding="utf-8")
    for name in ("TURNSTILE_SECRET", "EVAL_TOKEN", "ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_TOKEN"):
        assert name in text
        assert name not in wrangler.get("vars", {})


def test_worker_rate_limit_period_matches_wrangler(wrangler):
    limits = (WORKER_DIR / "limits.js").read_text(encoding="utf-8")
    period = int(re.search(r"RL_PERIOD_S = (\d+)", limits).group(1))
    assert period == wrangler["ratelimits"][0]["simple"]["period"]


def test_worker_never_logs_bodies():
    for path in WORKER_DIR.glob("*.js"):
        for call in re.findall(r"console\.\w+\(([^;]*)\);", path.read_text(encoding="utf-8")):
            assert not re.search(r"body|payload|text|forward|request", call), (path.name, call)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_worker_unit_tests_pass():
    result = subprocess.run(["node", "--test", "worker/test/"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
