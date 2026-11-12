"""Write worker/metrics/data.js and tests/fixtures/dashboard_cases.json.

The Worker port of the dashboard (worker/metrics/) cannot read YAML or text files, so its settings,
taxonomy codes and profanity list are written to data.js here. The fixture holds seeded datasets in
the shape of the Worker's D1 rows, together with what the PYTHON code computes for them: each
dataset is loaded into a temporary SQLite database through enps.storage and passed to
enps.metrics.aggregates.build_dashboard (and production.production_stats for the status numbers).
worker/test/metrics.test.js asserts the Worker port returns the same JSON for every case.

    .venv/bin/python scripts/build_dashboard_fixtures.py            # write both files
    .venv/bin/python scripts/build_dashboard_fixtures.py --check    # exit 1 when one is stale

Settings are read from config/settings.yaml without ENPS_* environment overrides, so the output
only depends on the repository.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sqlite3
import sys
import textwrap
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from enps import config  # noqa: E402
from enps.classify.types import ClassificationResult  # noqa: E402
from enps.metrics import aggregates, excerpt, production  # noqa: E402
from enps.storage import db  # noqa: E402
from enps.storage.timeutil import parse_iso, sp_day  # noqa: E402

DATA_JS = ROOT / "worker" / "metrics" / "data.js"
FIXTURE = ROOT / "tests" / "fixtures" / "dashboard_cases.json"
SETTINGS_FILE = ROOT / "config" / "settings.yaml"

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
START = NOW - timedelta(days=29)
SAO_PAULO = timezone(timedelta(hours=-3))

# Columns of the Worker's D1 submissions table that the dashboard reads; fixture rows are arrays
# in this order.
COLUMNS = (
    "public_id", "created_at", "score", "area", "tenure", "comment_anon", "status", "themes",
    "sentiment", "critical", "critical_types", "finished_at", "model", "prompt_version",
    "anonymizer_version",
)
# D1 statuses the Python schema does not have, mapped to the Python status that behaves the same.
PYTHON_STATUS = {"queued_quota": "queued"}
MODELS = ("@cf/google/gemma-4-26b-a4b-it", "@cf/zai-org/glm-4.7-flash")
PROMPTS = ("classify.v3+5e1f0a9c", "classify.v3+b72d4410")
ANONYMIZER = "edge-1.0.0"

DATA_HEADER = """\
// Settings, taxonomy codes and word lists of the Worker dashboard. Do not edit: this file is
// written by scripts/build_dashboard_fixtures.py from config/settings.yaml, config/taxonomy.yaml,
// config/profanity.txt and src/enps/metrics, and tests/test_dashboard_cases.py fails when it is
// stale.
"""

FIXTURE_DESCRIPTION = (
    "Dashboard and production cases shared by the Python metrics (tests/test_dashboard_cases.py) "
    "and the Worker port (worker/test/metrics.test.js). Written by "
    "scripts/build_dashboard_fixtures.py: rows are D1 submissions (arrays in the order of "
    "'columns'), expected is what src/enps/metrics computes for them with now = 'now'."
)

# ---------------------------------------------------------------- comment texts (anonymized)

PLAIN = (
    "Gosto muito da minha equipe, todo mundo se ajuda.",
    "O sistema cai toda semana na hora do pico e ninguém resolve.",
    "Faz dois anos que não tem reajuste e o mercado paga mais.",
    "Minha coordenadora [PESSOA] escuta a gente e defende o time.",
    "A escala 6x1 não deixa tempo pra família.",
    "Ninguém sabe o que precisa fazer pra ser promovido aqui.",
    "O plano de saúde é bom, uso com meus filhos.",
    "As mudanças chegam pelo corredor antes do comunicado oficial.",
    "Voltar pro presencial cinco dias foi um baque.",
    "Bati a meta o ano todo e ninguém falou nada.",
    "Tem semana que a gente fica até as nove da noite.",
    "Podiam colocar uma máquina de café no segundo andar.",
    "nada a declarar",
    "tá ok",
    "Empresa boa, salário ruim.",
)
CRITICAL_TEXTS = (
    "Meu gestor [PESSOA] grita comigo na frente de todo mundo.",
    "Não aguento mais, tô tomando remédio pra conseguir vir trabalhar.",
    "[PESSOA] fica comentando da minha roupa e eu mudo de caminho pra não cruzar com ele.",
    "Depois que voltei da licença maternidade me tiraram de todos os projetos.",
    "Ele disse que sabe onde eu moro, peço carona pra sair.",
    "seria mais fácil não acordar",
)
PROFANE = (
    "O gerente é um BABACA <b>sempre</b>",
    "Que PORRA, foda-se. Cú! desgracado; filho  da\nputa. Cultura, computador, disputa.",
    "Isso aqui tá uma merda e o [PESSOA] é um idiota.",
    "FILHO   DA PUTA do supervisor me ameaçou no estacionamento",
    "cu\u0301 de quem inventou esse banco de horas",
    "\u0130DIOTA quem aprovou a escala nova",
    "porra-feira de novo, pqp, vtnc",
    "_porra porra_ porra1 1porra \U0001F621idiota idiota\U0001F621",
    "\U0001D400porra \u216bporra porra\u200bporra",
    "putaria, puta, puto, putos e computador",
    "Arrombados!!! Todos uns otários, uns imbecis.",
)
SPACING = (
    "linha um\n\nlinha  dois\tfim",
    "  começa e termina com espaço   ",
    "espaço\u00a0duro e\u3000ideográfico\u2028separador",
    "\ufeffcom marca de ordem no início",
    "controle\x1cde\x1farquivo e\x85próxima linha",
    "\u2000\u2001\u200a só espaços largos \u202f\u205f",
    "Tô cansado \U0001F629\U0001F629\U0001F629 de verdade, " * 9,
)
MASK_ONLY = (
    "filho\u3000da\u2028puta",
    "pau\tno\ncu",
    "fodase e foda-se e foda",
    "FODA-SE!",
    "cuzão CUZÃO cuzao",
    "[PESSOA] falou com [LOCAL]",
    "\uff50\uff4f\uff52\uff52\uff41",
    "\ufb01lho da puta",
    "ß e \u01c5 e \u03a3",
    "",
)


def _long_text(rng: random.Random) -> str:
    return " ".join(rng.sample(PLAIN + CRITICAL_TEXTS + PROFANE, rng.randint(5, 9)))


# ---------------------------------------------------------------- data.js


@cache
def file_settings() -> dict[str, Any]:
    with open(SETTINGS_FILE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def dashboard_config() -> dict[str, Any]:
    """Defaults of the Worker dashboard: the settings and codes build_dashboard reads."""
    settings = file_settings()
    return {
        "min_group_size": settings["privacy"]["min_group_size"],
        "display_excerpt_chars": settings["privacy"]["display_excerpt_chars"],
        "areas": [a["code"] for a in settings["survey"]["areas"]],
        "tenures": [t["code"] for t in settings["survey"]["tenure_bands"]],
        "themes": config.theme_codes(),
        "sentiments": config.sentiment_codes(),
        "critical_types": config.critical_codes(),
    }


def profanity_alternatives() -> list[tuple[str, str]]:
    """(Python pattern, folded word) pairs in the order of the Python alternation.

    Python sorts the alternatives by pattern length only; equal lengths are ordered by the pattern
    here so the output is deterministic (they never match the same text).
    """
    pairs = {re.escape(excerpt.fold(w)).replace(r"\ ", r"\s+"): excerpt.fold(w) for w in excerpt.load_words()}
    return [(p, pairs[p]) for p in sorted(pairs, key=lambda p: (-len(p), p))]


def whitespace_class() -> str:
    """Body of a regex character class with every character Python's str.isspace() accepts."""
    points = [i for i in range(0x110000) if chr(i).isspace()]
    ranges: list[list[int]] = []
    for point in points:
        if ranges and point == ranges[-1][1] + 1:
            ranges[-1][1] = point
        else:
            ranges.append([point, point])
    if any(b > 0xFFFF for _, b in ranges):
        raise ValueError("whitespace outside the BMP")
    return "".join(f"\\u{a:04x}" if a == b else f"\\u{a:04x}-\\u{b:04x}" for a, b in ranges)


def _js_list(items: Sequence[str], width: int = 96) -> str:
    body = textwrap.wrap(", ".join(json.dumps(i, ensure_ascii=False) for i in items), width,
                         break_long_words=False, break_on_hyphens=False)
    return "[\n" + "\n".join(f"  {line}" for line in body) + "\n]"


def render_data_js() -> str:
    cfg = ",\n".join(f"  {key}: {json.dumps(value)}" for key, value in dashboard_config().items())
    words = [word for _, word in profanity_alternatives()]
    return "\n".join([
        DATA_HEADER,
        "// Defaults of buildDashboard: privacy settings, survey codes and taxonomy codes.",
        f"export const DASHBOARD_CONFIG = Object.freeze({{\n{cfg},\n}});",
        "",
        f"export const MAX_CRITICAL_CASES = {aggregates.MAX_CRITICAL_CASES};",
        f"export const MASK = {json.dumps(excerpt.MASK)};",
        f"export const ELLIPSIS = {json.dumps(excerpt.ELLIPSIS, ensure_ascii=False)};",
        "",
        "// Body of a regex character class: the characters Python's str.isspace() accepts.",
        f"export const WHITESPACE = {json.dumps(whitespace_class())};",
        "",
        f"// {len(words)} profanity words, folded (lowercase, no accents), longest pattern first.",
        f"export const PROFANITY = Object.freeze({_js_list(words)});",
        "",
    ])


# ---------------------------------------------------------------- seeded D1 rows


def stamp(dt: datetime, style: str = "z") -> str:
    """Timestamp text: "z" like Date.toISOString(), "utc" like timeutil.to_iso, "sp" at -03:00."""
    if style == "sp":
        return dt.astimezone(SAO_PAULO).isoformat(timespec="milliseconds")
    iso = dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return iso.replace("+00:00", "Z") if style == "z" else iso


class RowMaker:
    """Seeded submissions in the shape of the Worker's D1 rows."""

    def __init__(
        self, seed: int, *, rows_hint: int = 50, crit_rate: float = 0.1,
        comment_rate: float = 0.75, tie_rate: float = 0.05, long_rate: float = 0.03,
    ) -> None:
        self.rng = random.Random(seed)
        self.crit_rate = crit_rate
        self.comment_rate = comment_rate
        self.long_rate = long_rate
        self.tie_rate = tie_rate
        self.step_ms = max(2, 2 * 29 * 86_400_000 // max(rows_hint, 1))
        self.clock = START
        self.ids: set[str] = set()
        self.rows: list[dict[str, Any]] = []

    def _public_id(self) -> str:
        while True:
            public_id = f"{self.rng.getrandbits(40):010x}"
            if public_id not in self.ids:
                self.ids.add(public_id)
                return public_id

    def _tick(self) -> datetime:
        if self.rows and self.rng.random() < self.tie_rate:
            return self.clock  # same instant as the previous row
        self.clock += timedelta(milliseconds=self.rng.randint(1, self.step_ms))
        return self.clock

    def _comment(self) -> str:
        if self.rng.random() < self.long_rate:
            return _long_text(self.rng)
        pool = self.rng.choices((PLAIN, CRITICAL_TEXTS, PROFANE + SPACING), (70, 18, 12))[0]
        return self.rng.choice(pool)

    def _status(self, comment: str) -> str:
        if not comment:
            return "skipped"
        return self.rng.choices(
            ("done", "review", "processing", "queued_quota", "failed"), (82, 8, 3, 2, 5))[0]

    def _themes(self) -> list[str]:
        roll = self.rng.random()
        if roll < 0.05:
            return ["out"]
        picked = self.rng.sample([t for t in config.theme_codes() if t != "out"],
                                 self.rng.choice((1, 1, 1, 2, 2, 3, 4)))
        return picked + ["zz"] if roll > 0.99 else picked

    def _sentiment(self, score: int) -> str | None:
        if self.rng.random() < 0.03:
            return None
        weights = (70, 20, 10) if score >= 9 else (25, 45, 30) if score >= 7 else (5, 20, 75)
        return self.rng.choices(("pos", "neu", "neg"), weights)[0]

    def _wants_classification(self, status: str) -> bool:
        if status in ("done", "review"):
            return self.rng.random() > 0.03
        return status in ("failed", "skipped") and self.rng.random() < 0.08

    def add(
        self, area: str, tenure: str, score: int | None = None, *, comment: str | None = None,
        status: str | None = None, classify: bool | None = None, themes: list[str] | None = None,
        sentiment: str | None = None, types: list[str] | None = None, at: datetime | None = None,
        style: str = "z", **extra: Any,
    ) -> dict[str, Any]:
        """One row. `themes` forces a classification with these values; `extra` overrides columns."""
        rng = self.rng
        score = rng.randint(0, 10) if score is None else score
        if comment is None:
            comment = self._comment() if rng.random() < self.comment_rate else ""
        status = status or self._status(comment)
        created = at or self._tick()
        row = {
            "public_id": self._public_id(), "created_at": stamp(created, style), "score": score,
            "area": area, "tenure": tenure, "comment_anon": comment, "status": status,
            "themes": None, "sentiment": None, "critical": None, "critical_types": None,
            "finished_at": None, "model": None, "prompt_version": None,
            "anonymizer_version": ANONYMIZER if comment or rng.random() < 0.5 else "",
        }
        if themes is None and (classify if classify is not None else self._wants_classification(status)):
            themes = self._themes()
            sentiment = self._sentiment(score)
            critical = rng.random() < self.crit_rate
            types = rng.sample(config.critical_codes(), rng.choice((1, 1, 2))) if critical else []
        if themes is not None:
            row.update(
                themes=json.dumps(themes), sentiment=sentiment, critical=int(bool(types)),
                critical_types=json.dumps(types or []),
                finished_at=stamp(created + timedelta(milliseconds=rng.randint(1500, 120_000)), style),
                model=rng.choice(MODELS), prompt_version=rng.choice(PROMPTS),
            )
        row.update(extra)
        self.rows.append(row)
        return row


def random_rows(seed: int, n: int, **options: Any) -> list[dict[str, Any]]:
    """n rows with skewed area and tenure sizes, so some groups fall below k."""
    maker = RowMaker(seed, rows_hint=n, **options)
    areas, tenures = dashboard_config()["areas"], dashboard_config()["tenures"]
    area_weights = [maker.rng.choice((0, 0.2, 1, 3, 8)) for _ in areas]
    area_weights[maker.rng.randrange(len(areas))] = 8  # at least one sizeable area
    tenure_weights = [maker.rng.choice((0.1, 1, 2, 5)) for _ in tenures]
    for _ in range(n):
        maker.add(maker.rng.choices(areas, area_weights)[0], maker.rng.choices(tenures, tenure_weights)[0])
    return maker.rows


# ---------------------------------------------------------------- Python results


def _memory_db() -> sqlite3.Connection:
    return db.open_db(":memory:")


def load_rows(conn: sqlite3.Connection, dataset: str, rows: Sequence[dict[str, Any]]) -> None:
    """D1 rows into the Python schema: a classification row exactly when themes is set."""
    channel = "site" if dataset == "public" else "synthetic"
    for row in rows:
        response_id = db.insert_response(conn, {
            "public_id": row["public_id"], "dataset": dataset, "channel": channel,
            "submitted_at": row["created_at"], "score": row["score"], "area": row["area"],
            "tenure": row["tenure"], "comment_anon": row["comment_anon"],
            "anonymizer_version": row["anonymizer_version"],
            "status": PYTHON_STATUS.get(row["status"], row["status"]),
        })
        if row["themes"] is not None:
            result = ClassificationResult(
                status="done", themes=json.loads(row["themes"]), sentiment=row["sentiment"],
                critical=bool(row["critical"]), critical_types=json.loads(row["critical_types"]),
                model=row["model"], prompt_version=row["prompt_version"],
            )
            db.save_classification(conn, response_id, result, row["finished_at"])


def dashboard_case(
    name: str, rows: Sequence[dict[str, Any]], *, dataset: str = "public", **privacy: int,
) -> dict[str, Any]:
    """Rows, the cfg overrides of the Worker and the dashboard the Python code builds."""
    cfg = copy.deepcopy(file_settings())
    cfg["privacy"].update(privacy)
    conn = _memory_db()
    try:
        load_rows(conn, dataset, rows)
        expected = aggregates.build_dashboard(conn, dataset, cfg=cfg, now=NOW)
        visible = sorted(aggregates.visible_area_codes(conn, dataset, cfg))
    finally:
        conn.close()
    return {
        "name": name, "dataset": dataset, "cfg": privacy,
        "rows": [[row[c] for c in COLUMNS] for row in rows],
        "expected": expected, "visible_areas": visible,
    }


def production_case(
    name: str, daily: list[dict[str, Any]], latencies: list[Any], alerts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Status numbers the Python code computes from the same counters, latencies and alerts."""
    conn = _memory_db()
    try:
        counters: dict[str, dict[str, int]] = {}
        for row in daily:
            if row["key"] in db.STAT_COLUMNS:
                counters.setdefault(row["day"], {})[row["key"]] = row["value"]
        for day, values in counters.items():
            db.bump_daily(conn, day, "public", "site", **values)
        for item in latencies:
            if isinstance(item, int):
                ms, day = item, sp_day(NOW)
            else:
                created = parse_iso(item["created_at"])
                ms = (parse_iso(item["finished_at"]) - created) // timedelta(milliseconds=1)
                day = sp_day(created)
            db.add_latency_sample(conn, day=day, dataset="public", channel="site",
                                  queue_ms=ms // 3, processing_ms=ms - ms // 3)
        for i, alert in enumerate(alerts):
            delivered = alert["delivered_at"]
            conn.execute(
                "INSERT INTO alerts (response_public_id, dataset, critical_types, submitted_at, "
                "detected_at, mode, status, sent_at) VALUES (?, 'public', '[\"am\"]', ?, ?, "
                "'immediate', ?, ?)",
                (f"a{i:05d}", alert["submitted_at"], delivered or alert["submitted_at"],
                 "sent" if delivered else ("pending", "failed")[i % 2], delivered),
            )
        expected = production.production_stats(conn, "public")
    finally:
        conn.close()
    return {"name": name, "daily": daily, "latencies": latencies, "alerts": alerts,
            "expected": expected}


# ---------------------------------------------------------------- cases


def _scores(promoters: int, passives: int, detractors: int, rng: random.Random) -> list[int]:
    scores = ([rng.choice((9, 10)) for _ in range(promoters)] + [rng.choice((7, 8)) for _ in range(passives)]
              + [rng.randint(0, 6) for _ in range(detractors)])
    rng.shuffle(scores)
    return scores


def _small_cases(areas: list[str], tenures: list[str]) -> list[dict[str, Any]]:
    cases = [dashboard_case("empty dataset", [])]

    m = RowMaker(1)
    for score in (10, 3, 8):
        m.add("operacoes", "lt1", score, comment="")
    m.add("operacoes", "lt1", 2, comment=CRITICAL_TEXTS[0], status="done", themes=["lid"],
          sentiment="neg", types=["am"])
    cases.append(dashboard_case("below k: totals hidden, critical case listed without area", m.rows))

    m = RowMaker(2)
    for score in (9, 9, 7, 3, 10):
        m.add("comercial", "y3to5", score)
    cases.append(dashboard_case("total exactly k in a single cell", m.rows))

    m = RowMaker(3)
    for area in areas[:4]:
        m.add(area, "lt1")
    cases.append(dashboard_case("total k minus one", m.rows))

    m = RowMaker(4)
    for area in ("operacoes", "transporte", "atendimento"):
        for i in range(5):
            m.add(area, tenures[i % 4])
    cases.append(dashboard_case("three areas exactly at k", m.rows))
    cases.append(dashboard_case("three areas above k when k is 3", m.rows, min_group_size=3))

    m = RowMaker(5)
    for i in range(24):
        m.add(("tecnologia", "financeiro")[i % 2], tenures[i % 4], 9 + i % 2, comment="")
    cases.append(dashboard_case("all promoters", m.rows))

    m = RowMaker(6)
    for i in range(12):
        m.add("transporte", tenures[i % 2], i % 7)
    cases.append(dashboard_case("all detractors", m.rows))

    m = RowMaker(7)  # the fixture of tests/test_metrics.py
    ops = [("lt1", s) for s in (10, 10, 9, 9, 8, 3)] + [("gt5", s) for s in (9, 9, 7, 6, 5, 10)]
    for tenure, score in ops:
        m.add("operacoes", tenure, score, comment="")
    for tenure in ("lt1",) * 5 + ("gt5",) * 5:
        m.add("transporte", tenure, 9, comment="")
    m.add("tecnologia", "y1to3", 4, comment="")
    m.add("tecnologia", "y1to3", 9, comment="")
    m.add("tecnologia", "y1to3", 7, comment="ótimo time", status="done", themes=["eqp"],
          sentiment="pos", model="m1", prompt_version="classify.v1")
    m.add("atendimento", "y1to3", 2, comment=PROFANE[0], status="done", themes=["lid"],
          sentiment="neg", types=["am"])
    m.add("atendimento", "y1to3", 1, comment="")
    m.add("operacoes", "lt1", 0, comment="não aguento mais", status="review", themes=["car", "bem"],
          sentiment="neg", types=["bo"])
    cases.append(dashboard_case("fixture of the Python metrics tests", m.rows))
    return cases


def _merge_cases(tenures: list[str]) -> list[dict[str, Any]]:
    cases = []
    layouts = (
        ("small area merges with the smallest visible area", {"operacoes": 10, "transporte": 6, "atendimento": 3}),
        ("merge tie goes to the first area in display order",
         {"operacoes": 7, "transporte": 7, "atendimento": 9, "comercial": 2}),
        ("one big area and one tiny area are merged together", {"operacoes": 30, "juridico": 2}),
        ("every area below k, merged bucket shown",
         {"operacoes": 2, "transporte": 1, "atendimento": 2, "comercial": 1, "tecnologia": 2}),
        ("small groups already reach k together", {"operacoes": 12, "pessoas": 3, "juridico": 4}),
    )
    for seed, (name, layout) in enumerate(layouts, start=10):
        m = RowMaker(seed)
        for area, n in layout.items():
            for _ in range(n):
                m.add(area, m.rng.choice(tenures))
        cases.append(dashboard_case(name, m.rows))

    m = RowMaker(16)  # complementary suppression in the area x tenure table
    layout = {
        "operacoes": (6, 7, 2, 9), "transporte": (8, 5, 6, 6), "atendimento": (5, 5, 5, 5),
        "comercial": (1, 0, 0, 0),
    }
    for area, sizes in layout.items():
        for tenure, n in zip(tenures, sizes):
            for _ in range(n):
                m.add(area, tenure)
    cases.append(dashboard_case("complementary suppression in the cross table", m.rows))
    return cases


def _rounding_cases(areas: list[str]) -> list[dict[str, Any]]:
    cases = []
    layouts = (
        ("eNPS 12.5 rounds to 13", (1, 7, 0)),
        ("eNPS -12.5 rounds to -13", (0, 7, 1)),
        ("shares 6.25 and 18.75 round to even", (1, 3, 12)),
        ("shares 31.25 and 43.75 round to even", (5, 7, 4)),
        ("shares of 40 answers", (1, 13, 26)),
        ("eNPS -0.4 rounds to 0", (100, 49, 101)),
    )
    for seed, (name, groups) in enumerate(layouts, start=20):
        m = RowMaker(seed, rows_hint=sum(groups))
        for score in _scores(*groups, m.rng):
            area = "operacoes" if sum(groups) <= 16 else m.rng.choice(areas[:3])
            m.add(area, m.rng.choice(("lt1", "gt5")), score, comment="")
        cases.append(dashboard_case(name, m.rows, min_group_size=3))
    return cases


def _critical_cases(tenures: list[str]) -> list[dict[str, Any]]:
    cases = []
    m = RowMaker(30, crit_rate=0.0)
    for area, n in (("operacoes", 25), ("transporte", 12)):
        for _ in range(n):
            m.add(area, m.rng.choice(tenures))
    for area in ("juridico", "pessoas", "juridico", "pessoas", "transporte"):
        m.add(area, m.rng.choice(tenures), 2, comment=m.rng.choice(CRITICAL_TEXTS), status="done",
              themes=["lid", "cul"], sentiment="neg", types=["as"])
    m.add("operacoes", "gt5", 1, comment=PROFANE[3], status="review", themes=["bem"],
          sentiment="neg", types=["bo", "am"])
    cases.append(dashboard_case("critical cases in hidden areas have no area", m.rows))

    rows = random_rows(31, 180, crit_rate=0.8, comment_rate=1.0, tie_rate=0.3)
    cases.append(dashboard_case("more than 100 critical cases, ties keep row order", rows))

    m = RowMaker(32)
    texts = PROFANE + SPACING + CRITICAL_TEXTS
    for i, text in enumerate(texts):
        m.add(("operacoes", "transporte")[i % 2], tenures[i % 4], 3, comment=text, status="done",
              themes=["lid"], sentiment="neg", types=["am"])
    cases.append(dashboard_case("profanity and whitespace in excerpts", m.rows))

    m = RowMaker(33)
    for i in range(14):
        m.add(("operacoes", "comercial")[i % 2], tenures[i % 4], 4, comment=_long_text(m.rng),
              status="review", themes=["car"], sentiment="neg", types=["bo"])
    m.add("operacoes", "lt1", 4, comment=SPACING[-1], status="done", themes=["bem"], sentiment="neg", types=["bo"])
    cases.append(dashboard_case("long excerpts cut at 40 characters", m.rows,
                                min_group_size=3, display_excerpt_chars=40))
    cases.append(dashboard_case("long excerpts cut at 12 characters", m.rows,
                                min_group_size=3, display_excerpt_chars=12))

    m = RowMaker(34)
    boundary = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)
    moments = ((boundary - timedelta(milliseconds=1), "z"), (boundary, "z"),
               (boundary - timedelta(milliseconds=1), "utc"), (boundary + timedelta(hours=1), "sp"),
               (datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc), "utc"),
               (datetime(2026, 10, 31, 23, 59, 59, 999000, tzinfo=timezone.utc), "sp"))
    for i, (moment, style) in enumerate(moments * 2):
        m.add(("financeiro", "tecnologia")[i % 2], tenures[i % 3], 5, comment=CRITICAL_TEXTS[i % 6],
              status="done", themes=["lid"], sentiment="neg", types=["av"], at=moment, style=style)
    cases.append(dashboard_case("Sao Paulo day boundary and mixed timestamp formats", m.rows))
    return cases


def _edge_cases(tenures: list[str]) -> list[dict[str, Any]]:
    cases = []
    m = RowMaker(40)
    for i in range(8):
        m.add("operacoes", tenures[i % 4])
    for i in range(6):
        m.add("logistica", tenures[i % 2], comment=PLAIN[i], status="done", themes=["zz", "lid"],
              sentiment="neu", types=["xx", "di"] if i % 2 else [])
    for _ in range(2):
        m.add("operacoes", "gt10")
    cases.append(dashboard_case("codes missing from the config", m.rows, min_group_size=3))

    m = RowMaker(41)
    for _ in range(6):
        m.add("atendimento", "lt1", 9, comment=PLAIN[0], status="done", themes=["eqp"], sentiment="pos")
    m.add("atendimento", "lt1", 0, comment=CRITICAL_TEXTS[1], status="processing")
    m.add("atendimento", "lt1", 1, comment=CRITICAL_TEXTS[1], status="queued_quota")
    m.add("atendimento", "lt1", 2, comment=CRITICAL_TEXTS[0], status="failed", themes=["lid"],
          sentiment="neg", types=["am"])
    m.add("atendimento", "lt1", 3, comment="", status="skipped", themes=[], sentiment=None)
    m.add("atendimento", "lt1", 4, comment=CRITICAL_TEXTS[2], status="done", classify=False)
    m.add("atendimento", "lt1", 5, comment=CRITICAL_TEXTS[2], status="review", themes=["lid"],
          sentiment="neg", types=["as"])
    cases.append(dashboard_case("only done and review rows with a result count as classified", m.rows))

    m = RowMaker(42)
    stamps = ("2026-11-05T10:00:00.000Z", "2026-11-09T10:00:00.000Z", "2026-11-09T10:00:00.000Z",
              "2026-11-10T10:00:00.000Z", "2026-11-02T10:00:00.000Z")
    picks = (("m-a", "p1"), ("m-b", "p2"), ("m-c", None), (None, "p4"), ("m-e", "p5"))
    for i, (finished, (model, prompt)) in enumerate(zip(stamps, picks)):
        # Older anonymizer on the first rows (the latest classification included): the block
        # must report the version of the last row that has one.
        m.add("operacoes", tenures[i % 4], 7, comment=PLAIN[i], status="done", themes=["com"],
              sentiment="neu", finished_at=finished, model=model, prompt_version=prompt,
              anonymizer_version="edge-0.9.0" if i < 3 else ANONYMIZER)
    m.add("operacoes", "lt1", 8, comment=PLAIN[6], status="processing", model="m-x")
    m.add("operacoes", "lt1", 8, comment="", status="skipped", anonymizer_version="")
    cases.append(dashboard_case("pipeline block: latest classification and anonymizer", m.rows))
    return cases


def _random_cases() -> list[dict[str, Any]]:
    specs = (
        (101, 40, {}, {}), (102, 150, {}, {}), (103, 100, {}, {"min_group_size": 3}),
        (104, 200, {}, {"min_group_size": 8, "display_excerpt_chars": 120}),
        (105, 300, {}, {}), (106, 60, {}, {"min_group_size": 10}), (107, 25, {}, {"min_group_size": 2}),
        (108, 150, {"comment_rate": 0.95, "crit_rate": 0.3, "long_rate": 0.1}, {}),
    )
    cases = [
        dashboard_case(f"random dataset {seed} with {n} rows", random_rows(seed, n, **options), **privacy)
        for seed, n, options, privacy in specs
    ]
    cases.append(dashboard_case("random synthetic dataset", random_rows(109, 100), dataset="synthetic"))
    return cases


def dashboard_cases() -> list[dict[str, Any]]:
    cfg = dashboard_config()
    areas, tenures = cfg["areas"], cfg["tenures"]
    return [*_small_cases(areas, tenures), *_merge_cases(tenures), *_rounding_cases(areas),
            *_critical_cases(tenures), *_edge_cases(tenures), *_random_cases()]


def _daily_rows(rng: random.Random, days: int, *, zero_rate: float = 0.2) -> list[dict[str, Any]]:
    first = (NOW - timedelta(days=days)).date()
    rows = []
    for i in range(days):
        day = (first + timedelta(days=i)).isoformat()
        submissions = 0 if rng.random() < zero_rate else rng.randint(1, 80)
        rows += [
            {"day": day, "key": "submissions", "value": submissions},
            {"day": day, "key": "distinct_submitters", "value": rng.randint(0, submissions)},
            {"day": day, "key": "classified", "value": rng.randint(0, submissions)},
            {"day": day, "key": "critical", "value": rng.randint(0, 3)},
            {"day": day, "key": "rejected_rate_limit", "value": rng.randint(0, 5)},
            {"day": day, "key": "quota_hits", "value": rng.randint(0, 2)},
        ]
    rng.shuffle(rows)
    return rows


def _moment(rng: random.Random) -> datetime:
    return START + timedelta(milliseconds=rng.randint(0, 29 * 86_400_000))


def _latencies(rng: random.Random, n: int) -> list[dict[str, str]]:
    out = []
    for _ in range(n):
        created = _moment(rng)
        ms = rng.randint(800, 30_000) if rng.random() < 0.9 else rng.randint(30_000, 900_000)
        out.append({"created_at": stamp(created), "finished_at": stamp(created + timedelta(milliseconds=ms))})
    return out


def _alerts(rng: random.Random, n: int) -> list[dict[str, str | None]]:
    out = []
    for _ in range(n):
        submitted = _moment(rng)
        delivered = submitted + timedelta(milliseconds=rng.randint(500, 3_600_000))
        out.append({"submitted_at": stamp(submitted),
                    "delivered_at": None if rng.random() < 0.15 else stamp(delivered)})
    return out


def production_cases() -> list[dict[str, Any]]:
    cases = [production_case("nothing recorded", [], [], [])]
    base = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def at(ms: int) -> str:
        return stamp(base + timedelta(milliseconds=ms))

    cases.append(production_case(
        "one day",
        [{"day": "2026-11-03", "key": "submissions", "value": 3},
         {"day": "2026-11-03", "key": "distinct_submitters", "value": 2},
         {"day": "2026-11-03", "key": "classified", "value": 3}],
        [{"created_at": at(0), "finished_at": at(ms)} for ms in (4200, 6100, 12950)],
        [{"submitted_at": at(0), "delivered_at": at(9875)}],
    ))
    tie_ms = (2250, 750, 1250, 1750, 250)
    cases.append(production_case(
        "rounding ties of latencies and alert delays",
        [{"day": "2026-11-04", "key": "submissions", "value": len(tie_ms)}],
        [{"created_at": at(i), "finished_at": at(i + ms)} for i, ms in enumerate(tie_ms)],
        [{"submitted_at": at(0), "delivered_at": at(ms)} for ms in (1000, 1500)],
    ))
    rng = random.Random(50)
    cases.append(production_case(
        "latencies given in milliseconds",
        [{"day": "2026-11-05", "key": "submissions", "value": 9}],
        [rng.randint(500, 60_000) for _ in range(9)],
        [],
    ))
    rng = random.Random(51)
    zero_days = [{"day": f"2026-10-{d:02d}", "key": key, "value": 0 if key == "submissions" else 4}
                 for d in (12, 13) for key in ("submissions", "classified", "distinct_submitters")]
    cases.append(production_case(
        "days without submissions do not start the public period",
        zero_days + _daily_rows(rng, 10, zero_rate=0.0), _latencies(rng, 20), _alerts(rng, 5),
    ))
    rng = random.Random(52)
    cases.append(production_case("a month of traffic", _daily_rows(rng, 30), _latencies(rng, 150), _alerts(rng, 40)))
    rng = random.Random(53)
    latencies = [rng.randint(800, 30_000) if rng.random() < 0.9 else rng.randint(30_000, 900_000) for _ in range(3000)]
    cases.append(production_case("large volume", _daily_rows(rng, 30), latencies, _alerts(rng, 300)))
    return cases


def rounding_values() -> list[list[float]]:
    """Pairs [x, round(x, 1)] around the shares and seconds the dashboard and status show."""
    values = {100 * p / n for n in range(1, 41) for p in range(n + 1)}
    values |= {ms / 1000 for ms in range(0, 5001, 25)}
    values |= {q / 4 for q in range(-40, 41)}
    values |= {0.05, 0.15, 0.35, 2.675, 1.005, 0.04, -0.04, 123456.75, 4.5e14 + 0.75, 1e15 + 0.25, 1e21}
    rng = random.Random(60)
    values |= {rng.uniform(0, 1000) for _ in range(100)}
    return [[v, round(v, 1)] for v in sorted(values)]


def sum_values() -> list[list[Any]]:
    """Pairs [values, sum(values)]: alert delays in seconds, summed the Python 3.12 way."""
    rng = random.Random(65)
    lists = [[0.1] * 10, [1e16, 1.0, -1e16], [0.7, 0.1, 0.2]]
    lists += [[rng.randint(500, 3_600_000) / 1000 for _ in range(rng.randint(2, 60))] for _ in range(25)]
    return [[values, sum(values)] for values in lists]


def excerpt_values() -> dict[str, list[list[Any]]]:
    rng = random.Random(70)
    texts = [*PLAIN[:3], *CRITICAL_TEXTS[:2], *PROFANE, *SPACING, *(_long_text(rng) for _ in range(6))]
    return {
        "masks": [[t, excerpt.mask_profanity(t)] for t in (*PROFANE, *SPACING, *MASK_ONLY)],
        "excerpts": [[t, limit, excerpt.public_excerpt(t, limit)]
                     for t in texts for limit in (280, 60, 40, 12, 2, 1)],
    }


@cache
def build_fixture() -> dict[str, Any]:
    return {
        "description": FIXTURE_DESCRIPTION,
        "now": stamp(NOW),
        "columns": list(COLUMNS),
        "dashboards": dashboard_cases(),
        "production": production_cases(),
        "rounding": rounding_values(),
        "sums": sum_values(),
        **excerpt_values(),
    }


# ---------------------------------------------------------------- output


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _pretty(value: Any, indent: int = 0, width: int = 100) -> str:
    """JSON with one line per row: containers of scalars and short containers stay on one line."""
    flat = _compact(value)
    nested = isinstance(value, (dict, list)) and any(
        isinstance(v, (dict, list)) for v in (value.values() if isinstance(value, dict) else value))
    if not nested or indent + len(flat) <= width:
        return flat
    pad = " " * (indent + 1)
    if isinstance(value, dict):
        items = [f"{pad}{_compact(k)}: {_pretty(v, indent + 1, width)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    items = [pad + _pretty(v, indent + 1, width) for v in value]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


def render_fixture() -> str:
    return _pretty(build_fixture()) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether the files are stale")
    args = parser.parse_args(argv)
    stale = 0
    for path, content in ((DATA_JS, render_data_js()), (FIXTURE, render_fixture())):
        current = path.read_text(encoding="utf-8") if path.exists() else None
        name = path.relative_to(ROOT)
        if args.check:
            stale += current != content
            print(f"{name} is {'stale' if current != content else 'up to date'}")
        elif current != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {name} ({len(content.encode('utf-8'))} bytes)")
        else:
            print(f"{name} is up to date")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
