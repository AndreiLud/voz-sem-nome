"""Dashboard page: page hooks, strings used by the scripts, fixtures and the chart logic.

The chart logic (site/src/static/js/charts/model.js, scale.js, interact.js) is plain ES
modules; it runs in Node inside the node:24-bookworm-slim image when Docker is available,
without network. Those tests are skipped otherwise.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from enps import config
from enps.export import site
from enps.export.site_content import SRC_DIR, load_sources

ROOT = config.ROOT
JS_DIR = SRC_DIR / "static" / "js"
DASHBOARD_JS = [JS_DIR / "dashboard.js", *sorted((JS_DIR / "charts").glob("*.js"))]
FIXTURES = SRC_DIR / "fixtures"
NODE_IMAGE = "node:24-bookworm-slim"

# Keys built at run time from a template literal, with every value they can take.
DYNAMIC_KEYS = {
    "dashboard.units.": [f"{unit}_{form}" for unit in ("answers", "comments") for form in ("one", "other")],
    "dashboard.labels.": ["promoters", "passives", "detractors"],
    "dashboard.datasets.": ["synthetic", "public"],
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> site.BuildReport:
    out = tmp_path_factory.mktemp("dash") / "public"
    missing = out.parent / "missing.json"
    return site.build(out, metrics_path=missing, dashboard_path=missing, environ={})


@pytest.fixture(scope="module")
def sources():
    return load_sources(SRC_DIR)


def _scripts() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in DASHBOARD_JS)


def _lookup(tree, dotted: str):
    for part in dotted.split("."):
        if not isinstance(tree, dict) or part not in tree:
            return None
        tree = tree[part]
    return tree


# ------------------------------------------------------------------ page and assets


def test_dashboard_page_has_every_hook_the_script_uses(built):
    used = set(re.findall(r"slot\(\"([a-z-]+)\"", _scripts()))
    assert {"content", "status", "freshness", "small-state", "production"} <= used
    for rel in ("painel/index.html", "en/dashboard/index.html"):
        html = (built.out / rel).read_text(encoding="utf-8")
        for name in used:
            assert f'data-slot="{name}"' in html, (rel, name)
        assert 'id="viz-keys-hint"' in html
        assert re.search(r'data-slot="content" hidden', html), "panels wait for data"
        assert html.count('aria-pressed="true"') == 1 and 'data-dataset="public"' in html


def test_chart_modules_and_styles_are_published(built):
    for path in DASHBOARD_JS:
        rel = path.relative_to(JS_DIR)
        assert (built.out / "assets" / "js" / rel).is_file(), rel
    css = (built.out / "assets" / "css" / "site.css").read_text(encoding="utf-8")
    assert ".divbars__bar" in css and ".heat__cell--l6" in css and ".viz-tip" in css


def test_scripts_never_write_html_or_inline_styles():
    """Labels come from data: text nodes only. The CSP forbids style attributes."""
    for path in DASHBOARD_JS:
        text = path.read_text(encoding="utf-8")
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                       'setAttribute("style"', "style: "):
            assert banned not in text, (path.name, banned)


# ------------------------------------------------------------------ strings


def test_every_dashboard_string_the_scripts_use_exists(sources):
    text = _scripts()
    keys = set(re.findall(r"[\"'`](dashboard\.[a-z0-9_.]+)[\"'`]", text))
    for prefix, values in DYNAMIC_KEYS.items():
        assert prefix + "${" in text or any(prefix + v in keys for v in values), prefix
        keys |= {prefix + value for value in values}
    keys = {k for k in keys if not k.endswith(".")}
    assert len(keys) > 60
    for lang in sources.languages:
        strings = {"dashboard": sources.strings[lang]["dashboard"]}
        missing = sorted(k for k in keys if not isinstance(_lookup(strings, k), str))
        assert not missing, (lang, missing)


def test_dashboard_strings_are_all_used(sources):
    """No dead strings: every leaf under "dashboard" is read by the template or a script."""
    template = (SRC_DIR / "templates" / "dashboard.html.j2").read_text(encoding="utf-8")
    text = _scripts()

    def leaves(tree, prefix=""):
        for key, value in tree.items():
            path = f"{prefix}.{key}" if prefix else key
            yield from leaves(value, path) if isinstance(value, dict) else [path]

    dynamic = {prefix[len("dashboard."):] + v for prefix, values in DYNAMIC_KEYS.items() for v in values}
    for path in leaves(sources.strings["pt"]["dashboard"]):
        used = (f"dashboard.{path}" in text or path in dynamic or f"d.{path}" in template)
        assert used, path


# ------------------------------------------------------------------ fixtures


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["dashboard-synthetic.json", "dashboard-public.json", "dashboard-public-small.json"])
def test_dashboard_fixtures_follow_the_contract(name):
    data = _fixture(name)
    k = data["min_group_size"]
    assert data["sample"] is True and data["dataset"] in ("synthetic", "public")
    assert set(data) >= {"generated_at", "totals", "by_area", "other_areas", "by_tenure", "other_tenures",
                         "area_tenure", "themes", "critical", "pipeline"}
    small = data["totals"]["responses"] is None
    if small:
        assert all(v is None for v in data["totals"].values())
        assert data["by_area"] == [] and data["by_tenure"] == []
    for group in [*data["by_area"], *data["by_tenure"]]:
        assert group["n"] >= k
    for other in (data["other_areas"], data["other_tenures"]):
        assert other is None or (other["codes"] is None and other["n"] >= k)
    visible = {a["code"] for a in data["by_area"]}
    for case in data["critical"]["cases"]:
        assert case["area"] is None or case["area"] in visible
        assert set(case) == {"id", "types", "themes", "area", "excerpt", "date"}


def test_status_fixture_has_the_live_demo_fields():
    """The dev server mock answers /status with this file: the Worker's flat shape."""
    data = _fixture("status.json")
    assert data["sample"] is True
    assert set(data) - {"sample", "generated_at"} == {
        "pending", "processing", "eta_seconds", "analyzed_today", "latency_p50_s", "latency_p95_s",
        "critical_today", "alerts_recorded", "quota_exhausted_today", "production"}
    assert isinstance(data["quota_exhausted_today"], bool)
    assert {"total_submissions", "distinct_submitters", "classified", "first_public_day"} <= set(data["production"])


def test_live_panel_never_talks_about_a_server_queue(sources):
    for lang in sources.languages:
        d = sources.strings[lang]["dashboard"]
        assert "queue" not in d
        text = " ".join([d["sections"]["production"], d["section_notes"]["production"],
                         *d["live"].values(), *d["production"].values()]).lower()
        for word in ("fila", "queue", "servidor", "server", "worker online", "analisador", "analyzer"):
            assert word not in text, (lang, word)


# ------------------------------------------------------------------ chart logic in Node


def _node_available() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "image", "inspect", NODE_IMAGE], capture_output=True, check=False)
    return probe.returncode == 0


NODE_TESTS = r"""
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as m from "__JS__/charts/model.js";
import * as s from "__JS__/charts/scale.js";
import { nextIndex } from "__JS__/charts/interact.js";

const QUERY = __QUERY__;
const fixture = (name) => JSON.parse(readFileSync(`__FIXTURES__/${name}`, "utf8"));
const label = (code) => `L:${code}`;

test("eNPS axis is symmetric and holds every value", () => {
  assert.equal(s.symmetricDomain([-43, 50, 21]), 50);
  assert.equal(s.symmetricDomain([0]), 10);
  assert.equal(s.symmetricDomain([-60]), 75);
  assert.equal(s.symmetricDomain([130, null]), 100);
  assert.deepEqual(s.barExtent(-50, 50), { start: 0, size: 50 });
  assert.deepEqual(s.barExtent(25, 50), { start: 50, size: 25 });
  assert.equal(s.position(0, 25), 50);
  assert.deepEqual(s.divergingTicks(50), [-50, 0, 50]);
});

test("heat classes use nice steps and spread over the ramp", () => {
  const scale = s.heatScale(0.29);
  assert.deepEqual(scale, { step: 0.05, count: 6 });
  assert.equal(s.heatClass(0, scale), 0);
  assert.equal(s.heatClass(0.05, scale), 1);
  assert.equal(s.heatClass(0.1, scale), 2);
  assert.equal(s.heatClass(0.101, scale), 3);
  assert.equal(s.heatClass(0.9, scale), 6);
  assert.deepEqual(s.heatScale(0), { step: 0.01, count: 0 });
  assert.equal(s.heatScale(1).count <= s.HEAT_MAX_CLASSES, true);
  assert.equal(s.heatLevel(1, 3), 1);
  assert.equal(s.heatLevel(3, 3), 6);
  assert.equal(s.heatLevel(1, 1), 4);
  assert.equal(s.heatLevel(0, 6), 0);
  assert.deepEqual(s.heatBounds({ step: 0.1, count: 2 }), [[0, 0.1], [0.1, 0.2]]);
});

test("dataset lives in the URL in each language's words", () => {
  assert.equal(m.datasetFromSearch("?base=publica", "pt", QUERY), "public");
  assert.equal(m.datasetFromSearch("?dataset=public", "en", QUERY), "public");
  assert.equal(m.datasetFromSearch("?dataset=public", "pt", QUERY), "public");
  assert.equal(m.datasetFromSearch("?base=outra", "pt", QUERY), "synthetic");
  assert.equal(m.datasetFromSearch("", "en", QUERY), "synthetic");
  assert.equal(m.searchWithDataset("?x=1&dataset=public", "synthetic", "pt", QUERY), "?x=1&base=sintetica");
  assert.equal(m.searchWithDataset("", "public", "en", QUERY), "?dataset=public");
});

test("groups are sorted by eNPS with the merged group last", () => {
  const data = fixture("dashboard-synthetic.json");
  const rows = m.groupRows(data.by_area, data.other_areas, { label, otherLabel: "Other", sort: true });
  const named = rows.filter((r) => !r.merged);
  assert.equal(named.length, data.by_area.length);
  for (let i = 1; i < named.length; i += 1) assert.ok(named[i - 1].enps >= named[i].enps);
  assert.equal(rows.at(-1).key, m.OTHER_KEY);
  assert.equal(rows.at(-1).label, "Other");
  const tenures = m.groupRows(data.by_tenure, null, { label, otherLabel: "x", sort: false });
  assert.deepEqual(tenures.map((r) => r.key), data.by_tenure.map((g) => g.code));
  const ends = m.extremes(rows);
  assert.equal(ends.top.key, named[0].key);
  assert.equal(ends.bottom.key, named.at(-1).key);
});

test("small datasets show nothing", () => {
  assert.equal(m.isSmall(fixture("dashboard-public-small.json"), 5), true);
  assert.equal(m.isSmall(fixture("dashboard-public.json"), 5), false);
  assert.equal(m.isDashboard(fixture("dashboard-public.json")), true);
  assert.equal(m.isDashboard({ error: "x" }), false);
});

test("overview parts use the percentages of the contract", () => {
  const parts = m.overviewParts(fixture("dashboard-synthetic.json").totals);
  assert.deepEqual(parts.map((p) => p.key), ["promoters", "passives", "detractors"]);
  assert.ok(Math.abs(parts.reduce((sum, p) => sum + p.share, 0) - 1) < 0.002);
});

test("themes come sorted by mentions, without unmentioned ones", () => {
  const data = fixture("dashboard-public.json");
  const rows = m.themeRows(data.themes, label);
  for (let i = 1; i < rows.length; i += 1) assert.ok(rows[i - 1].n >= rows[i].n);
  assert.ok(rows.every((r) => r.n > 0));
  assert.equal(rows.length + m.unmentionedThemes(data.themes).length, data.themes.length);
});

test("heat cells are shares of each area's classified comments", () => {
  const data = fixture("dashboard-synthetic.json");
  const codes = m.themeRows(data.themes, label).map((r) => r.key);
  const matrix = m.heatMatrix(data, codes, { label, otherLabel: "Other" });
  const first = data.by_area[0];
  const total = first.sentiment.pos + first.sentiment.neu + first.sentiment.neg;
  const cell = matrix.rows[0].cells[0];
  assert.equal(cell.total, total);
  assert.equal(cell.share, first.themes[codes[0]] / total);
  assert.equal(matrix.columns.at(-1).key, m.OTHER_KEY);
  const peak = m.heatPeak(matrix);
  assert.equal(peak.cell.share, matrix.max);

  const empty = { ...data, by_area: [{ ...first, sentiment: { pos: 0, neu: 0, neg: 0 } }], other_areas: null };
  const none = m.heatMatrix(empty, codes, { label, otherLabel: "Other" });
  assert.deepEqual(none.columns, []);
  assert.deepEqual(none.empty, [`L:${first.code}`]);
  assert.equal(m.heatPeak(none), null);
});

test("live demo status: daily numbers, latency and the quota flag", () => {
  const live = m.normalizeStatus({
    pending: 0, processing: 1, eta_seconds: 4, analyzed_today: 12, latency_p50_s: 3.5, latency_p95_s: 9,
    critical_today: 1, alerts_recorded: 2, quota_exhausted_today: true, production: { total_submissions: 9 },
  });
  assert.deepEqual(
    { ...live, production: null },
    { analyzedToday: 12, latencyP50: 3.5, latencyP95: 9, criticalToday: 1, alertsRecorded: 2,
      quotaExhausted: true, production: null });
  assert.equal(live.production.total_submissions, 9);
  const mock = m.normalizeStatus(fixture("status.json"));
  assert.equal(mock.quotaExhausted, false);
  for (const key of ["analyzedToday", "latencyP50", "latencyP95", "criticalToday", "alertsRecorded"]) {
    assert.equal(typeof mock[key], "number", key);
  }
  assert.equal(typeof mock.production.first_public_day, "string");
});

test("status of the local mode API has no daily numbers, and junk is rejected", () => {
  const local = m.normalizeStatus({
    queue: { pending: 3, processing: 1, eta_seconds_new_job: 40 }, worker: { heartbeat_age_seconds: 5 },
    production: { total_submissions: 9 },
  });
  assert.equal(local.analyzedToday, null);
  assert.equal(local.latencyP50, null);
  assert.equal(local.quotaExhausted, false);
  assert.equal(local.production.total_submissions, 9);
  assert.equal(m.normalizeStatus({ quota_exhausted_today: "yes" }).quotaExhausted, false);
  assert.equal(m.normalizeStatus({ error: "offline" }), null);
  assert.equal(m.normalizeStatus(null), null);
});

test("critical counts keep the configured order", () => {
  const counts = m.criticalByType({ by_type: { am: 2, bo: 1 } }, ["am", "as", "bo"]);
  assert.deepEqual(counts, [{ code: "am", n: 2 }, { code: "as", n: 0 }, { code: "bo", n: 1 }]);
});

test("arrow keys move in a list and in a grid", () => {
  assert.equal(nextIndex("ArrowDown", 0, 5), 1);
  assert.equal(nextIndex("ArrowUp", 0, 5), 0);
  assert.equal(nextIndex("End", 1, 5), 4);
  assert.equal(nextIndex("Enter", 1, 5), null);
  assert.equal(nextIndex("ArrowRight", 2, 9, 3), 2);
  assert.equal(nextIndex("ArrowRight", 3, 9, 3), 4);
  assert.equal(nextIndex("ArrowDown", 4, 9, 3), 7);
  assert.equal(nextIndex("ArrowDown", 7, 9, 3), 7);
  assert.equal(nextIndex("Home", 5, 9, 3), 3);
  assert.equal(nextIndex("End", 3, 8, 3), 5);
  assert.equal(nextIndex("ArrowRight", 7, 8, 3), 7);
});
"""


@pytest.mark.skipif(not _node_available(), reason="needs Docker with the node:24-bookworm-slim image")
def test_chart_logic_in_node(sources):
    code = (NODE_TESTS
            .replace("__JS__", JS_DIR.as_uri())
            .replace("__FIXTURES__", str(FIXTURES))
            .replace("__QUERY__", json.dumps(sources.site["query"])))
    src = str(SRC_DIR)
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "-v", f"{src}:{src}:ro", NODE_IMAGE,
         "node", "--input-type=module", "-"],
        input=code, capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert re.search(r"\bfail 0\b", result.stdout), result.stdout[-2000:]
