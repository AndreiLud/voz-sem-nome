import random
import secrets
from datetime import datetime, timezone

import pytest

from enps import config
from enps.classify.types import ClassificationResult
from enps.metrics import enps
from enps.metrics.aggregates import build_dashboard, visible_area_codes
from enps.metrics.excerpt import mask_profanity, public_excerpt, truncate
from enps.metrics.suppression import partition_groups, suppress_cells
from enps.storage import db
from enps.storage.timeutil import to_iso

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
AREAS = config.area_codes()
TENURES = config.tenure_codes()


# ---------------------------------------------------------------- eNPS


def test_enps_all_promoters_and_empty():
    assert enps.summarize([9, 10, 10])["enps"] == 100
    assert enps.summarize([9, 10, 10])["pct_promoters"] == 100.0
    assert enps.summarize([0, 6, 3])["enps"] == -100
    empty = enps.summarize([])
    assert empty["n"] == 0 and empty["enps"] is None and empty["pct_detractors"] is None


def test_enps_categories_and_rounding():
    assert [enps.category(s) for s in (0, 6, 7, 8, 9, 10)] == [
        "detractor", "detractor", "passive", "passive", "promoter", "promoter"]
    with pytest.raises(ValueError):
        enps.category(11)
    assert enps.summarize([10] + [7] * 7)["enps"] == 13     # 12.5 rounds away from zero
    assert enps.summarize([0] + [7] * 7)["enps"] == -13
    assert enps.summarize([10, 10, 3])["enps"] == 33


# ---------------------------------------------------------------- suppression


def test_small_group_merges_with_smallest_visible():
    part = partition_groups({"a": 3, "b": 10, "c": 6, "d": 0}, 5)
    assert part.visible == ("b",) and part.merged == ("a", "c") and part.other_n == 9


def test_total_below_k_hides_everything():
    part = partition_groups({"a": 2, "b": 1}, 5)
    assert part.visible == () and part.other_n == 3 and not part.other_shown


@pytest.mark.parametrize("seed", range(4))
def test_subtraction_attack_on_one_dimension(seed):
    rng = random.Random(seed)
    sizes = [0, 0, 1, 2, 3, 4, 5, 6, 7, 9, 12, 30]
    for _ in range(2500):
        k = rng.choice([2, 3, 5, 8])
        counts = {f"g{i}": rng.choice(sizes) for i in range(rng.randint(1, 9))}
        part = partition_groups(counts, k)
        total = sum(counts.values())
        hidden = [c for c in counts if c not in part.visible and counts[c] > 0]
        assert all(counts[c] >= k for c in part.visible)
        assert total - sum(counts[c] for c in part.visible) == part.other_n
        assert sorted(hidden) == sorted(part.merged)
        if total >= k:
            # Total minus visible groups is never the size of a single hidden group.
            assert len(hidden) != 1
            assert part.other_n == 0 or part.other_n >= k
        else:  # the total itself is suppressed and nothing is listed
            assert part.visible == () and not part.other_shown


@pytest.mark.parametrize("seed", range(4))
def test_cross_table_has_no_line_with_one_suppressed_cell(seed):
    rng = random.Random(seed)
    sizes = [0, 0, 1, 2, 3, 4, 5, 6, 8, 15]
    for _ in range(600):
        k = rng.choice([3, 5])
        counts = {(a, t): rng.choice(sizes) for a in AREAS for t in TENURES}
        areas = partition_groups({a: sum(counts[a, t] for t in TENURES) for a in AREAS}, k)
        tenures = partition_groups({t: sum(counts[a, t] for a in AREAS) for t in TENURES}, k)
        suppressed = suppress_cells(
            counts, AREAS, TENURES, k,
            hidden_rows=[a for a in AREAS if a not in areas.visible],
            hidden_cols=[t for t in TENURES if t not in tenures.visible],
        )
        lines = [[(a, t) for t in TENURES] for a in AREAS] + \
                [[(a, t) for a in AREAS] for t in TENURES]
        for line in lines:
            assert sum(cell in suppressed for cell in line) != 1
        for (a, t), n in counts.items():
            if (a, t) not in suppressed:
                assert n >= k and a in areas.visible and t in tenures.visible


# ---------------------------------------------------------------- aggregates


@pytest.fixture
def conn(tmp_path):
    connection = db.open_db(tmp_path / "metrics.db")
    yield connection
    connection.close()


def insert(conn, area, tenure, score, *, comment="", result=None, dataset="synthetic"):
    rid = db.insert_response(conn, {
        "public_id": secrets.token_hex(5), "dataset": dataset, "channel": "synthetic",
        "submitted_at": to_iso(NOW), "score": score, "area": area, "tenure": tenure,
        "comment_anon": comment, "anonymizer_version": "anon-1" if comment else "",
        "status": "done" if result else "skipped",
    })
    if result:
        db.save_classification(conn, rid, result, to_iso(NOW))
    return rid


def fixture_rows(conn):
    ops = [("lt1", s) for s in (10, 10, 9, 9, 8, 3)] + [("gt5", s) for s in (9, 9, 7, 6, 5, 10)]
    for tenure, score in ops:
        insert(conn, "operacoes", tenure, score)
    for tenure in ("lt1",) * 5 + ("gt5",) * 5:
        insert(conn, "transporte", tenure, 9)
    insert(conn, "tecnologia", "y1to3", 4)
    insert(conn, "tecnologia", "y1to3", 9)
    insert(conn, "tecnologia", "y1to3", 7, comment="ótimo time", result=ClassificationResult(
        status="done", themes=["eqp"], sentiment="pos", model="m1", quantization="Q4_K_M",
        prompt_version="classify.v1"))
    insert(conn, "atendimento", "y1to3", 2, comment="O gerente é um BABACA <b>sempre</b>",
           result=ClassificationResult(status="done", themes=["lid"], sentiment="neg",
                                       critical=True, critical_types=["am"]))
    insert(conn, "atendimento", "y1to3", 1)
    insert(conn, "operacoes", "lt1", 0, comment="não aguento mais", result=ClassificationResult(
        status="review", themes=["car", "bem"], sentiment="neg", critical=True,
        critical_types=["bo"]))


def test_dashboard_on_small_fixture(conn):
    fixture_rows(conn)
    data = build_dashboard(conn, "synthetic", now=NOW)
    assert data["min_group_size"] == 5 and data["generated_at"] == to_iso(NOW)
    assert data["totals"]["responses"] == 28 and data["totals"]["with_comment"] == 3
    assert data["totals"]["critical"] == 2
    by_area = {g["code"]: g for g in data["by_area"]}
    assert set(by_area) == {"operacoes", "transporte"}
    ops = by_area["operacoes"]
    assert (ops["n"], ops["promoters"], ops["passives"], ops["detractors"]) == (13, 7, 2, 4)
    assert ops["enps"] == 23 and ops["critical"] == 1 and ops["themes"]["car"] == 1
    assert data["other_areas"]["n"] == 5 and data["other_areas"]["codes"] is None
    assert {g["code"] for g in data["by_tenure"]} == {"lt1", "gt5", "y1to3"}
    assert data["other_tenures"] is None
    cells = {(c["area"], c["tenure"]): c for c in data["area_tenure"]}
    assert len(cells) == len(AREAS) * len(TENURES)
    shown = {key for key, c in cells.items() if not c.get("suppressed")}
    assert shown == {("operacoes", "lt1"), ("operacoes", "gt5"),
                     ("transporte", "lt1"), ("transporte", "gt5")}
    assert cells["operacoes", "lt1"]["n"] == 7
    themes = {t["code"]: t for t in data["themes"]}
    assert themes["lid"]["n"] == 1 and themes["lid"]["critical"] == 1
    assert themes["eqp"]["sentiment"]["pos"] == 1
    cases = {c["types"][0]: c for c in data["critical"]["cases"]}
    assert data["critical"]["total"] == 2 and data["critical"]["by_type"]["am"] == 1
    assert cases["am"]["area"] is None  # atendimento is merged into the other bucket
    assert cases["am"]["excerpt"] == "O gerente é um [ofensivo] <b>sempre</b>"
    assert cases["bo"]["area"] == "operacoes" and cases["bo"]["date"] == "2026-11-11"
    assert data["pipeline"] == {"model": "m1", "quantization": "Q4_K_M",
                                "prompt_version": "classify.v1", "anonymizer_version": "anon-1"}
    assert visible_area_codes(conn, "synthetic") == {"operacoes", "transporte"}


def test_dashboard_below_k_shows_no_group(conn):
    for score in (10, 3, 8):
        insert(conn, "operacoes", "lt1", score)
    data = build_dashboard(conn, "synthetic", now=NOW)
    assert all(value is None for value in data["totals"].values())
    assert data["by_area"] == [] and data["other_areas"] is None
    assert data["by_tenure"] == [] and data["other_tenures"] is None
    assert all(cell.get("suppressed") for cell in data["area_tenure"])
    empty = build_dashboard(conn, "public", now=NOW)
    assert empty["totals"]["responses"] is None and empty["critical"]["cases"] == []


@pytest.mark.parametrize("seed", range(3))
def test_dashboard_never_reveals_one_hidden_area(conn, seed):
    rng = random.Random(seed)
    for _ in range(rng.randint(5, 60)):
        insert(conn, rng.choice(AREAS[:5]), rng.choice(TENURES), rng.randint(0, 10))
    data = build_dashboard(conn, "synthetic", now=NOW)
    total = data["totals"]["responses"]
    if total is None:
        return
    other = data["other_areas"]["n"] if data["other_areas"] else 0
    assert total - sum(g["n"] for g in data["by_area"]) == other
    assert other == 0 or other >= 5


# ---------------------------------------------------------------- excerpts


def test_profanity_mask_is_accent_insensitive_and_whole_word():
    text = "Que PORRA, foda-se. Cú! desgracado; filho  da\nputa. Cultura, computador, disputa."
    assert mask_profanity(text) == (
        "Que [ofensivo], [ofensivo]. [ofensivo]! [ofensivo]; [ofensivo]. "
        "Cultura, computador, disputa.")
    assert mask_profanity("[PESSOA] falou com [LOCAL]") == "[PESSOA] falou com [LOCAL]"


def test_excerpt_limit():
    long_text = "palavra " * 100
    excerpt = public_excerpt(long_text, 50)
    assert len(excerpt) <= 50 and excerpt.endswith("…")
    assert truncate("curto", 50) == "curto"
    assert public_excerpt("linha um\n\nlinha  dois", 280) == "linha um linha dois"
