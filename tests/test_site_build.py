"""Static site build: pages, language twins, links, writing rules and the dev server."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from enps import config
from enps.export import site
from enps.export.site_content import SRC_DIR, fmt_hours, fmt_number, fmt_percent, fmt_signed, key_paths, leaf_strings, load_sources
from enps.export.site_data import estimate

ROOT = config.ROOT
SITE_DIR = ROOT / "site"
TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/api.js"
BANNED_PT = ["vale ressaltar", "é importante destacar", "em suma", "mergulhe", "desvende",
             "jornada", "crucial", "robusto"]
BANNED_EN = ["delve", "tapestry", "showcase", "passionate", "leverage", "seamless", "elevate",
             "journey", "crucial", "robust", "cutting-edge"]
DASHES = ("—", "–", " - ")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
TEXT_ATTRS = ("alt", "aria-label", "title", "content", "placeholder", "data-label", "data-copy-label")
BINARY = {".woff2", ".png", ".jpg", ".webp", ".avif", ".ico"}


def _load_dev_server():
    spec = importlib.util.spec_from_file_location("dev_server", ROOT / "scripts" / "dev_server.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["dev_server"] = module  # dataclasses look the module up while it loads
    spec.loader.exec_module(module)
    return module


dev_server = _load_dev_server()


# ------------------------------------------------------------------ HTML parsing


class Page(HTMLParser):
    """Text nodes, text attributes, links, ids and h1 count of one HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.links: list[tuple[str, str, dict[str, str]]] = []  # (tag, url, attrs)
        self.ids: set[str] = set()
        self.h1 = 0
        self.lang = ""
        self.toggle: dict[str, str] = {}
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "h1":
            self.h1 += 1
        if "id" in a:
            self.ids.add(a["id"])
        if a.get("id") == "lang-toggle":
            self.toggle = a
        for name in TEXT_ATTRS:
            if a.get(name) and not (tag == "meta" and a.get("name") in ("viewport", "color-scheme", "theme-color")):
                self.texts.append(a[name])
        for name in ("href", "src"):
            if a.get(name):
                self.links.append((tag, a[name], a))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.texts.append(" ".join(data.split()))


def parse(path: Path) -> Page:
    page = Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> site.BuildReport:
    out = tmp_path_factory.mktemp("site") / "public"
    missing = out.parent / "missing.json"
    return site.build(out, metrics_path=missing, dashboard_path=missing, environ={})


@pytest.fixture(scope="module")
def sources():
    return load_sources(SRC_DIR)


def _html_files(out: Path) -> list[Path]:
    return sorted(out.rglob("*.html"))


# ------------------------------------------------------------------ pages and twins


def test_every_page_exists_in_both_languages(built, sources):
    for page in site.PAGES:
        for lang in sources.languages:
            assert (built.out / site.output_file(sources.page_path(page, lang))).is_file(), (page, lang)
    assert len(built.pages) == len(site.PAGES) * len(sources.languages)


def test_language_toggle_points_to_the_twin_page(built, sources):
    for page in site.PAGES:
        for lang in sources.languages:
            other = sources.other_language(lang)
            parsed = parse(built.out / site.output_file(sources.page_path(page, lang)))
            twin = "home" if page == "not_found" else page
            assert parsed.toggle.get("href") == sources.page_path(twin, other), (page, lang)
            assert parsed.toggle.get("data-lang") == other
            assert parsed.lang == sources.site["html_lang"][lang]


def test_pages_have_one_h1_and_hreflang_alternates(built, sources):
    for path in _html_files(built.out):
        parsed = parse(path)
        assert parsed.h1 == 1, path
        if path.name != "404.html":
            alternates = [a for tag, url, a in parsed.links if tag == "link" and a.get("rel") == "alternate"]
            assert {a["hreflang"] for a in alternates} == {"pt-BR", "en", "x-default"}, path


def test_i18n_files_have_the_same_keys(sources):
    pt, en = (key_paths(sources.strings[lang]) for lang in ("pt", "en"))
    assert pt == en, sorted(pt ^ en)


# ------------------------------------------------------------------ writing rules


def _all_texts(built, sources) -> list[tuple[str, str]]:
    texts = [(str(path), text) for path in _html_files(built.out) for text in parse(path).texts]
    for lang in sources.languages:
        texts += [(f"i18n/{lang}.yaml", text) for text in leaf_strings(sources.strings[lang])]
    return texts


def test_no_dashes_or_emojis_in_text(built, sources):
    for where, text in _all_texts(built, sources):
        for dash in DASHES:
            assert dash not in text, f"{where}: {text!r}"
        assert not EMOJI.search(text), f"{where}: {text!r}"


def test_no_banned_words(built, sources):
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED_PT + BANNED_EN) + r")\b", re.I)
    for where, text in _all_texts(built, sources):
        assert not pattern.search(text), f"{where}: {text!r}"


def test_no_ai_tool_names_anywhere_in_site(built):
    # Stored as hex so that this file does not contain the names it looks for.
    names = [bytes.fromhex(h).decode() for h in ("636c61756465", "616e7468726f706963")]
    pattern = re.compile("|".join(names), re.I)
    roots = [SITE_DIR, built.out]
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix not in BINARY:
                text = path.read_text(encoding="utf-8", errors="replace")
                assert not pattern.search(text), path
                assert not pattern.search(str(path.relative_to(root))), path


# ------------------------------------------------------------------ links and resources


def _target(out: Path, url: str) -> Path | None:
    """File served for an internal URL, following redirects like Cloudflare does."""
    parts = urlsplit(url)
    path = parts.path
    for _ in range(3):
        route = dev_server.resolve(out, path)
        if route.location:
            path = urlsplit(route.location).path
            continue
        return route.file if route.status == 200 else None
    return None


def test_every_internal_link_resolves(built):
    out = built.out
    for page_path in _html_files(out):
        parsed = parse(page_path)
        for tag, url, _ in parsed.links:
            parts = urlsplit(url)
            if parts.scheme or url.startswith("//") or url.startswith("mailto:"):
                continue
            if not parts.path:  # same-page anchor
                assert parts.fragment in parsed.ids, f"{page_path}: {url}"
                continue
            assert parts.path.startswith("/"), f"{page_path}: relative URL {url}"
            target = _target(out, parts.path)
            assert target is not None, f"{page_path}: {url}"
            if parts.fragment and target.suffix == ".html":
                assert parts.fragment in parse(target).ids, f"{page_path}: {url}"
            if parts.fragment and target.suffix == ".svg":
                assert f'id="{parts.fragment}"' in target.read_text(encoding="utf-8"), url


def test_css_urls_resolve(built):
    css = (built.out / "assets" / "css" / "site.css").read_text(encoding="utf-8")
    assert "@import" not in css
    for url in re.findall(r"url\(\"?([^\")]+)\"?\)", css):
        if url.startswith("#"):
            continue
        assert url.startswith("/") and _target(built.out, url) is not None, url


def test_no_external_scripts_or_styles_except_turnstile(built):
    for page_path in _html_files(built.out):
        for tag, url, attrs in parse(page_path).links:
            loads_code = tag == "script" or (tag == "link" and attrs.get("rel") in ("stylesheet", "preload", "modulepreload"))
            if loads_code and urlsplit(url).scheme:
                assert url == TURNSTILE, f"{page_path}: {url}"
    headers = (built.out / "_headers").read_text(encoding="utf-8")
    assert "script-src 'self' https://challenges.cloudflare.com" in headers
    assert "frame-src https://challenges.cloudflare.com" in headers


def test_no_inline_styles_or_scripts(built):
    """The CSP allows no inline code; JSON data blocks are not executed."""
    for page_path in _html_files(built.out):
        html = page_path.read_text(encoding="utf-8")
        assert " style=" not in html, page_path
        assert "<style" not in html, page_path
        for match in re.finditer(r"<script([^>]*)>", html):
            attrs = match.group(1)
            assert "src=" in attrs or 'type="application/json"' in attrs, page_path


# ------------------------------------------------------------------ data files and determinism


def test_data_files(built, sources):
    data = built.out / "data"
    cfg = json.loads((data / "config.json").read_text(encoding="utf-8"))
    privacy = config.settings()["privacy"]
    assert cfg["min_group_size"] == privacy["min_group_size"]
    assert cfg["max_comment_chars"] == privacy["max_comment_chars"]
    assert cfg["api_base"] == "/api/v1/public"
    assert cfg["languages"] == ["pt", "en"]
    assert cfg["turnstile_site_key"] == sources.site["turnstile_site_key"]
    assert cfg["routes"]["dashboard"] == {"pt": "/painel/", "en": "/en/dashboard/"}
    assert cfg["sample"] == {"metrics": True, "dashboard_synthetic": True}
    taxonomy = json.loads((data / "taxonomy.json").read_text(encoding="utf-8"))
    assert [t["code"] for t in taxonomy["themes"]] == config.theme_codes()
    assert [a["code"] for a in taxonomy["areas"]] == config.area_codes()
    assert {"pt", "en"} <= set(taxonomy["critical_types"][0]["label"])
    dashboard = json.loads((data / "dashboard-synthetic.json").read_text(encoding="utf-8"))
    assert dashboard["sample"] is True and dashboard["dataset"] == "synthetic"
    sitemap = (built.out / "sitemap.xml").read_text(encoding="utf-8")
    assert sitemap.count("<loc>") == 8 and "404" not in sitemap
    assert "Sitemap: https://" in (built.out / "robots.txt").read_text(encoding="utf-8")


def test_build_is_deterministic(built, tmp_path):
    again = tmp_path / "public"
    missing = tmp_path / "missing.json"
    site.build(again, metrics_path=missing, dashboard_path=missing, environ={})
    first = {p.relative_to(built.out): p.read_bytes() for p in built.out.rglob("*") if p.is_file()}
    second = {p.relative_to(again): p.read_bytes() for p in again.rglob("*") if p.is_file()}
    assert first == second


def test_turnstile_key_from_environment(tmp_path):
    missing = tmp_path / "missing.json"
    report = site.build(tmp_path / "public", metrics_path=missing, dashboard_path=missing,
                        environ={"TURNSTILE_SITE_KEY": "0x4AAAAexample"})
    assert not report.turnstile_test_key
    html = (tmp_path / "public" / "enviar" / "index.html").read_text(encoding="utf-8")
    assert 'data-sitekey="0x4AAAAexample"' in html


def _build_with(tmp_path: Path, metrics: dict) -> tuple[str, str, dict]:
    """Build with a metrics file and the fixture dashboard; return pt home, en home, config."""
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps(metrics), encoding="utf-8")
    out = tmp_path / "public"
    site.build(out, metrics_path=path, dashboard_path=tmp_path / "missing.json", environ={})
    cfg = json.loads((out / "data" / "config.json").read_text(encoding="utf-8"))
    return ((out / "index.html").read_text(encoding="utf-8"),
            (out / "en" / "index.html").read_text(encoding="utf-8"), cfg)


def _section(html: str, start: str, end: str) -> str:
    return html[html.index(start):html.index(end)]


def _headline(html: str) -> str:
    return _section(html, 'id="results-live"', 'class="compare"')


def _compare(html: str) -> str:
    return _section(html, 'class="compare"', 'class="estimate"')


def test_sample_badge_only_for_fixtures(built, tmp_path):
    home = (built.out / "index.html").read_text(encoding="utf-8")
    assert home.count("badge--sample") == 2  # results and dashboard preview
    sample = json.loads((SRC_DIR / "fixtures" / "metrics.sample.json").read_text(encoding="utf-8"))
    assert sample["sample"] is True and set(sample["modes"]) == {"edge", "local"}

    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({"modes": {"edge": {"classification": {"n": 100, "critical": {
        "tp": 20, "fn": 2, "fp": 3, "recall": 0.909, "precision": 0.87}}}}}), encoding="utf-8")
    dashboard = tmp_path / "dash.json"
    dashboard.write_text(json.dumps({"dataset": "synthetic", "totals": {
        "responses": 10, "enps": 20, "promoters": 4, "passives": 4, "detractors": 2,
        "pct_promoters": 40.0, "pct_passives": 40.0, "pct_detractors": 20.0}}), encoding="utf-8")
    site.build(tmp_path / "public", metrics_path=metrics, dashboard_path=dashboard, environ={})
    real = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "badge--sample" not in real
    assert "91%" in _headline(real) and "87%" in _headline(real) and "+20" in real
    assert _headline(real).count("badge--pending") == 5  # the five numbers missing from metrics.json
    copied = json.loads((tmp_path / "public" / "data" / "dashboard-synthetic.json").read_text(encoding="utf-8"))
    assert copied["totals"]["enps"] == 20


def test_results_show_the_live_demo_and_compare_with_local_mode(tmp_path):
    edge = {
        "pipeline": {"model": "@cf/google/gemma-4-26b-a4b-it", "prompt_version": "classify.v3+abcd1234",
                     "anonymizer_version": "edge-1.0.0"},
        "anonymization": {"n_spans": 104, "recall": 0.9135, "n_comments": 60, "comments_with_leak": 6},
        "performance": {"status": "measured", "n_comments": 100,
                        "latency": {"mean_s": 2.5, "p50_s": 2.2, "p95_s": 5.1}},
    }
    local = {
        "pipeline": {"model": "gemma4:e2b-it-qat", "anonymizer_version": "1.0.0"},
        "anonymization": {"n_spans": 104, "recall": 0.9712, "n_comments": 60, "comments_with_leak": 2},
        "performance": {"steady": {"n": 99, "mean_s": 6.0, "p50_s": 5.8, "p95_s": 9.9},
                        "hardware": {"logical_cpus": 4, "mem_total_gib": 15.6}},
    }
    pt, en, cfg = _build_with(tmp_path, {"live_mode": "edge", "modes": {"edge": edge, "local": local}})
    head, table = _headline(pt), _compare(pt)
    assert "91%" in head and "2,2 s" in head and "5,1 s" in head  # edge numbers in the tiles
    assert "97%" not in head and "97%" in table and "5,8 s" in table and "2,2 s" in table
    assert "Demo (Cloudflare) vs modo local (servidor próprio)" in table
    assert table.count("Ainda não medido") == 8  # classification of both modes: 4 rows x 2
    assert "Gemma 4 26B A4B no Workers AI" in table and "Gemma 4 E2B no Ollama" in table
    assert "Demo: modelo @cf/google/gemma-4-26b-a4b-it, prompt classify.v3+abcd1234" in pt
    assert "Modo local: modelo gemma4:e2b-it-qat, anonimizador 1.0.0." in pt
    assert "4 vCPUs" in table
    assert "Not measured yet" in _compare(en) and "Demo (Cloudflare) vs local mode (own server)" in en
    # The estimator uses the live demo's mean time when it exists.
    assert cfg["estimator"]["machine_seconds_per_comment"] == 2.5
    assert "média medida no Workers AI" in pt


def test_missing_numbers_are_never_invented(tmp_path):
    pt, en, cfg = _build_with(tmp_path, {"modes": {"edge": {}, "local": {}}})
    assert _headline(pt).count("badge--pending") == 7
    assert _compare(pt).count("Ainda não medido") == 14 and _compare(en).count("Not measured yet") == 14
    assert cfg["estimator"]["machine_seconds_per_comment"] is None
    assert "Ainda não medido" in _section(pt, 'class="estimate"', "</form>")


def test_version_1_metrics_are_read_as_local_mode(tmp_path):
    v1 = {"pipeline": {"model": "gemma4:e2b-it-qat"},
          "anonymization": {"n_spans": 104, "recall": 0.9712, "n_comments": 340, "n_comments_with_pii": 60,
                            "comments_with_leak": 2},
          "performance": {"steady": {"n": 10, "mean_s": 7.0, "p50_s": 6.5, "p95_s": 8.0}}}
    pt, _, cfg = _build_with(tmp_path, v1)
    assert _headline(pt).count("badge--pending") == 7
    assert "97%" in _compare(pt) and "6,5 s" in _compare(pt)
    assert "3%" in _compare(pt) and "(n = 60)" in _compare(pt)  # leaks among comments that had data
    assert cfg["estimator"]["machine_seconds_per_comment"] == 7.0
    assert "média medida na CPU do modo local" in pt


def test_copy_describes_the_cloudflare_demo_and_the_local_mode(built, sources):
    stale = re.compile(r"IA local|local AI|modelo local|local model|no próprio servidor separa|"
                       r"running on the server itself|monitora a fila|monitors the queue", re.I)
    for lang in sources.languages:
        how = sources.strings[lang]["home"]["how"]
        assert set(how["steps"]) == {"input", "rules", "ner", "lexicon", "llm", "verifier", "storage",
                                     "dashboard", "alert"}
        for text in leaf_strings(sources.strings[lang]):
            assert not stale.search(text), (lang, text)
    home = parse(built.out / "index.html")
    text = " ".join(home.texts)
    assert "Gemma 4 26B A4B" in text and "Gemma 4 E2B" in text and "Workers AI" in text
    assert "Banco D1" in text and "Modo local" in text and "spaCy" in text
    diagram = (built.out / "index.html").read_text(encoding="utf-8")
    diagram = diagram[diagram.index('<svg class="diagram"'):diagram.index("</svg>", diagram.index('<svg class="diagram"'))]
    for title in ("Regras no Worker", "Nomes e lugares", "Workers AI", "Banco D1", "Alerta", "modo local"):
        assert title in diagram, title


OFFICIAL_PAGES = (
    "https://developers.cloudflare.com/workers-ai/platform/data-usage/",
    "https://www.cloudflare.com/gdpr/subprocessors/cloudflare-services/",
    "https://developers.cloudflare.com/d1/configuration/data-location/",
    "https://developers.cloudflare.com/d1/reference/time-travel/",
    "https://www.cloudflare.com/turnstile-privacy-policy/",
    "https://www.cloudflare.com/cloudflare-customer-dpa/",
    "https://developers.cloudflare.com/workers/observability/logs/workers-logs/",
)


def test_privacy_page_cites_the_official_cloudflare_pages(built, sources):
    retention = config.settings()["privacy"]["public_retention_days"]
    for lang in sources.languages:
        page = built.out / site.output_file(sources.page_path("privacy", lang))
        parsed = parse(page)
        urls = {url for _, url, _ in parsed.links}
        assert set(OFFICIAL_PAGES) <= urls, lang
        text = " ".join(parsed.texts)
        assert "Cloudflare does not use Customer Content processed by Workers AI to train AI models" in text
        assert "does not retain inputs or outputs after they are no longer needed to process the request" in text
        assert "the Customer is the Controller" in text and "Cloudflare is a Processor" in text
        assert f" {retention + 7} " in text  # last day a deleted row can still be restored
        assert ("orientação jurídica" if lang == "pt" else "legal advice") in text
        for word in ("controlador", "operador", "Time Travel", "Turnstile", "user agent", "TLS"):
            assert word in text, (lang, word)
    doc = (ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")
    for url in OFFICIAL_PAGES:
        assert url in doc, url
    for dash in DASHES:
        assert dash not in doc, dash


# ------------------------------------------------------------------ formatting and estimate


def test_number_formatting():
    assert fmt_number(1234.5, "pt", 1) == "1.234,5"
    assert fmt_number(1234.5, "en", 1) == "1,234.5"
    assert fmt_signed(-8, "pt") == "−8" and fmt_signed(24, "en") == "+24" and fmt_signed(0, "pt") == "0"
    assert fmt_percent(0.873, "pt", 1) == "87,3%"
    assert fmt_hours(33.04, "pt") == "33,0" and fmt_hours(1234.4, "en") == "1,234"


def test_estimate_with_default_assumptions(sources):
    result = estimate(sources.site["estimator"], machine_s=6.0)
    assert result.comments == pytest.approx(900)
    assert result.manual_hours == pytest.approx(37.5)
    assert result.review_hours == pytest.approx(4.5)
    assert result.saved_hours == pytest.approx(33.0)
    assert result.machine_hours == pytest.approx(1.5)
    assert estimate(sources.site["estimator"], machine_s=None).machine_hours is None


# ------------------------------------------------------------------ dev server


def test_dev_server_routing(built):
    out = built.out
    resolve = dev_server.resolve
    assert resolve(out, "/").file == out / "index.html"
    assert resolve(out, "/painel", "base=publica").location == "/painel/?base=publica"
    assert resolve(out, "/painel/index.html").location == "/painel/"
    assert resolve(out, "/404.html").location == "/404"
    missing_en = resolve(out, "/en/nada")
    assert missing_en.status == 404 and missing_en.file == out / "en" / "404.html"
    assert resolve(out, "/nada/mais").file == out / "404.html"
    assert resolve(out, "/_headers").status == 404
    assert resolve(out, "/../etc/passwd").status == 404


def test_dev_server_headers_drop_https_only_rules(built):
    rules = dev_server.parse_headers_file((built.out / "_headers").read_text(encoding="utf-8"))
    headers = dev_server.headers_for(rules, "/assets/fonts/x.woff2")
    assert "immutable" in headers["Cache-Control"]
    assert "upgrade-insecure-requests" not in headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in headers


def test_dev_server_proxy_maps_to_the_api_router():
    api = (ROOT / "src" / "enps" / "api" / "public.py").read_text(encoding="utf-8")
    router_prefix = re.search(r'APIRouter\(prefix="([^"]+)"\)', api).group(1)
    assert dev_server.upstream_target("/api/v1/public/status") == f"{router_prefix}/status"
    assert dev_server.upstream_target("/api/v1/public/dashboard", "dataset=public") == (
        f"{router_prefix}/dashboard?dataset=public")


def _post(api, body):
    return api.handle("POST", "/api/v1/public/submissions", "", json.dumps(body).encode())


VALID = {"score": 4, "area": "operacoes", "tenure": "lt1", "consent": True, "turnstile": "t", "lang": "pt"}


def test_mock_submission_moves_through_the_queue():
    now = [1000.0]
    api = dev_server.MockApi(clock=lambda: now[0], wall=lambda: 0.0)
    status, data, _ = _post(api, {**VALID, "comment": "Meu chefe grita comigo todo dia na frente de todos."})
    assert status == 202 and data["status"] == "queued" and re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", data["code"])
    assert data["queue_position"] == 2 and "result" not in data  # the API's shape
    path = f"/api/v1/public/submissions/{data['code']}"
    assert api.handle("GET", path, "", b"")[1]["status"] == "queued"
    now[0] += 6
    assert api.handle("GET", path, "", b"")[1]["status"] == "processing"
    now[0] += 7
    done = api.handle("GET", path, "", b"")[1]
    assert done["status"] == "done" and done["critical"] is True and done["themes"]


def test_mock_errors():
    api = dev_server.MockApi(clock=lambda: 0.0, wall=lambda: 0.0)
    comment = "Um comentário longo o bastante para contar como duplicado."
    assert _post(api, {**VALID, "comment": comment})[0] == 202
    assert _post(api, {**VALID, "comment": comment})[0] == 409
    assert _post(api, {**VALID, "comment": "x #429"})[2]["Retry-After"] == "240"
    assert _post(api, {**VALID, "consent": False})[0] == 422
    assert _post(api, {**VALID, "turnstile": ""})[0] == 403
    assert _post(api, {**VALID})[0] == 202  # third accepted call in the window
    assert _post(api, {**VALID, "comment": "quarto envio"})[0] == 429
    assert api.handle("GET", "/api/v1/public/submissions/ZZZZ-ZZZZ", "", b"")[0] == 404
    assert api.handle("GET", "/api/v1/public/submissions/TEST-CR7T", "", b"")[1]["critical_types"] == ["am", "bo"]
    small = dev_server.MockApi(public_variant="small")
    assert small.handle("GET", "/api/v1/public/dashboard", "dataset=public", b"")[1]["totals"]["enps"] is None


def test_release_build_refuses_sample_data(tmp_path):
    from enps.export import site
    missing = tmp_path / "no-metrics.json"
    code = site.main(["--out", str(tmp_path / "public"), "--metrics", str(missing),
                      "--dashboard", str(tmp_path / "no-dashboard.json"), "--release"])
    assert code == 1
