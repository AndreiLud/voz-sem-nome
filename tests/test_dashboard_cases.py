"""Dashboard cases shared with the Worker port of src/enps/metrics.

tests/fixtures/dashboard_cases.json holds seeded datasets in the shape of the Worker's D1 rows and
what the Python metrics compute for them; worker/test/metrics.test.js asserts the Worker port
(worker/metrics/) returns the same JSON. These tests keep the fixture and worker/metrics/data.js in
step with the Python code and the config files, and check the assumptions the port is built on.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_dashboard_fixtures as fx  # noqa: E402

from enps import config  # noqa: E402
from enps.metrics import excerpt  # noqa: E402

STALE = "is stale: run .venv/bin/python scripts/build_dashboard_fixtures.py"


def _fixture() -> dict:
    return json.loads(fx.FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_current():
    assert fx.FIXTURE.read_text(encoding="utf-8") == fx.render_fixture(), f"dashboard_cases.json {STALE}"


def test_worker_data_is_current():
    assert fx.DATA_JS.read_text(encoding="utf-8") == fx.render_data_js(), f"worker/metrics/data.js {STALE}"


def test_worker_config_matches_the_python_config():
    cfg = fx.dashboard_config()
    assert cfg["areas"] == config.area_codes() and cfg["tenures"] == config.tenure_codes()
    assert cfg["themes"] == config.theme_codes() and cfg["critical_types"] == config.critical_codes()


def test_profanity_alternatives_are_the_python_ones():
    pattern = excerpt._pattern(str(excerpt.PROFANITY_FILE)).pattern
    body = pattern[pattern.index("(?:") + 3: pattern.rindex(")(?!")]
    assert sorted(body.split("|")) == sorted(p for p, _ in fx.profanity_alternatives())
    ordered = [len(p) for p, _ in fx.profanity_alternatives()]
    assert ordered == sorted(ordered, reverse=True), "longest pattern first, like the Python alternation"


def test_python_word_class_is_letters_numbers_and_underscore():
    """excerpt.js writes Python's \\w as [\\p{L}\\p{N}_]; this holds for the running Python."""
    word = re.compile(r"\w")
    for point in range(0x110000):
        ch = chr(point)
        expected = unicodedata.category(ch)[0] in "LN" or ch == "_"
        assert bool(word.match(ch)) == expected, hex(point)


def test_fixture_covers_the_hard_cases():
    data = _fixture()
    k_default = fx.dashboard_config()["min_group_size"]
    dashboards = data["dashboards"]

    def has(predicate) -> bool:
        return any(predicate(case) for case in dashboards)

    assert has(lambda c: not c["rows"]), "empty dataset"
    assert has(lambda c: 0 < len(c["rows"]) < c["cfg"].get("min_group_size", k_default)), "below k"
    assert has(lambda c: any(g["n"] == c["expected"]["min_group_size"] for g in c["expected"]["by_area"])), \
        "area exactly at k"
    assert has(lambda c: c["expected"]["totals"]["enps"] == 100), "all promoters"
    assert has(lambda c: c["expected"]["other_areas"] is not None), "merged areas shown"
    assert has(lambda c: any(x["area"] is None for x in c["expected"]["critical"]["cases"])
               and c["expected"]["totals"]["responses"] is not None), "critical case in a hidden area"
    assert has(lambda c: c["expected"]["critical"]["total"] > len(c["expected"]["critical"]["cases"])), \
        "more critical cases than the list shows"
    assert has(lambda c: any("[ofensivo]" in x["excerpt"] for x in c["expected"]["critical"]["cases"])), \
        "profanity in an excerpt"
    assert has(lambda c: any(x["excerpt"].endswith(excerpt.ELLIPSIS) for x in c["expected"]["critical"]["cases"])), \
        "truncated excerpt"
    assert has(lambda c: c["expected"]["totals"]["pct_promoters"] == 6.2), "share 6.25 rounded to even"
    assert has(lambda c: any(cell.get("suppressed") for cell in c["expected"]["area_tenure"])
               and any(not cell.get("suppressed") for cell in c["expected"]["area_tenure"])), \
        "cross table with shown and suppressed cells"
    assert {"queued_quota", "processing", "failed", "skipped"} <= {
        row[data["columns"].index("status")] for c in dashboards for row in c["rows"]}
    assert len(data["production"]) >= 5 and len(data["rounding"]) > 500 and len(data["sums"]) > 20
