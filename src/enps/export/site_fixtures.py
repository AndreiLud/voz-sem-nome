"""Sample fixtures for the site and the dev server, built with the real dashboard code.

    python -m enps.export.site_fixtures

Writes site/src/fixtures/dashboard-*.json from seeded fake answers through
enps.metrics.aggregates.build_dashboard, so the fixtures always follow the contract.
Every file carries "sample": true and the pages show a "sample data" badge for it.
Nothing here is a measurement.
"""
from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from enps.export.site_data import FIXTURES_DIR
from enps.metrics.aggregates import build_dashboard
from enps.storage import db
from enps.storage.timeutil import to_iso

GENERATED_AT = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
SAMPLE_MODEL = {"model": "qwen3.5:2b-q4_K_M", "quantization": "Q4_K_M",
                "prompt_version": "classify.v1", "anonymizer_version": "1.0.0"}

# Area -> (relative size, mean score). Juridico and financeiro are small on purpose:
# they show how small groups are merged.
AREAS = {
    "operacoes": (34, 7.1), "transporte": (26, 6.4), "atendimento": (20, 7.4),
    "comercial": (14, 7.9), "tecnologia": (10, 8.7), "pessoas": (5, 8.3),
    "financeiro": (2, 7.6), "juridico": (0.6, 8.0),
}
TENURES = {"lt1": 22, "y1to3": 38, "y3to5": 22, "gt5": 18}
THEMES_BY_MOOD = {
    "neg": ["car", "lid", "rem", "inf", "com", "flx", "cre", "rec", "bem"],
    "neu": ["inf", "com", "flx", "cre", "rem", "out"],
    "pos": ["eqp", "lid", "cul", "bem", "rem", "cre", "rec"],
}

# Anonymized comments in the voice of employees. Fictitious, used as critical excerpts.
CRITICAL_COMMENTS: Sequence[tuple[str, tuple[str, ...], tuple[str, ...]]] = (
    ("Meu coordenador [PESSOA] grita comigo na frente de todo mundo e vive dizendo que qualquer um faz meu trabalho.", ("am",), ("lid",)),
    ("Não durmo direito faz meses, tomo remédio pra conseguir vir trabalhar e choro no carro antes de entrar.", ("bo",), ("bem", "car")),
    ("O supervisor da base de [LOCAL] fica comentando da minha roupa e insiste pra sair com ele depois do turno.", ("as",), ("lid",)),
    ("Depois que voltei da licença maternidade me tiraram dos projetos e ninguém explica o motivo.", ("di",), ("cre", "cul")),
    ("Teve briga no pátio semana passada e o cara disse que sabe onde eu moro. Agora peço carona pra sair.", ("av",), ("eqp",)),
    ("Chefe chama a gente de burro no grupo da equipe. Já reclamei e piorou.", ("am",), ("lid", "com")),
    ("Tem dia que penso que seria mais fácil não acordar. Ninguém aqui percebe.", ("bo",), ("bem",)),
    ("Piada com sotaque nordestino todo dia na reunião, e o gestor [PESSOA] ri junto.", ("di",), ("cul", "lid")),
)
PLAIN_COMMENTS = {
    "neg": [
        "Faz dois anos sem reajuste e a carga só aumenta.",
        "Desde que [PESSOA] saiu, faço o trabalho de duas pessoas.",
        "O sistema de roteirização cai toda segunda de manhã.",
        "A gente descobre as mudanças pelo corredor antes do comunicado.",
    ],
    "neu": [
        "A escala mudou de novo, ainda estou me adaptando.",
        "Seria bom ter mais treinamento no sistema novo.",
        "Nada a declarar.",
    ],
    "pos": [
        "Minha equipe se ajuda muito, é o que me segura aqui.",
        "A coordenadora [PESSOA] escuta a gente e dá retorno.",
        "Gostei do plano de saúde novo.",
    ],
}


@dataclass(frozen=True)
class FakeAnswer:
    score: int
    area: str
    tenure: str
    comment: str
    themes: tuple[str, ...]
    sentiment: str | None
    critical_types: tuple[str, ...]
    submitted_at: datetime


def _weighted(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _score(rng: random.Random, mean: float) -> int:
    return max(0, min(10, round(rng.gauss(mean, 1.9))))


def _mood(score: int) -> str:
    return "pos" if score >= 9 else ("neu" if score >= 7 else "neg")


def fake_answers(n: int, seed: int, *, start: datetime, comment_share: float = 0.6,
                 critical_rate: float = 0.04) -> list[FakeAnswer]:
    rng = random.Random(seed)
    sizes = {code: size for code, (size, _) in AREAS.items()}
    answers = []
    for i in range(n):
        area = _weighted(rng, sizes)
        score = _score(rng, AREAS[area][1])
        when = start + timedelta(minutes=37 * i + rng.randint(0, 30))
        mood = _mood(score)
        if rng.random() < critical_rate:
            text, types, themes = rng.choice(CRITICAL_COMMENTS)
            answers.append(FakeAnswer(min(score, 5), area, _weighted(rng, TENURES), text, themes,
                                      "neg", types, when))
            continue
        if rng.random() >= comment_share:
            answers.append(FakeAnswer(score, area, _weighted(rng, TENURES), "", (), None, (), when))
            continue
        themes = tuple(sorted(set(rng.sample(THEMES_BY_MOOD[mood], k=rng.choice((1, 1, 2, 3))))))
        if "out" in themes:
            themes = ("out",)
        answers.append(FakeAnswer(score, area, _weighted(rng, TENURES), rng.choice(PLAIN_COMMENTS[mood]),
                                  themes, mood, (), when))
    return answers


def dashboard_for(answers: Sequence[FakeAnswer], dataset: str) -> dict:
    """Run the real aggregation over the fake answers in an in-memory database."""
    conn = db.connect(":memory:")
    db.init_db(conn)
    for i, a in enumerate(answers, start=1):
        status = "done" if a.comment else "skipped"
        rid = db.insert_response(conn, {
            "public_id": f"sample{i:04d}", "dataset": dataset,
            "channel": "synthetic" if dataset == "synthetic" else "site",
            "submitted_at": to_iso(a.submitted_at), "score": a.score, "area": a.area,
            "tenure": a.tenure, "comment_anon": a.comment, "comment_chars": len(a.comment),
            "anonymizer_version": SAMPLE_MODEL["anonymizer_version"], "status": status,
        })
        if a.comment:
            conn.execute(
                "INSERT INTO classifications (response_id, themes, sentiment, critical, critical_types,"
                " model, quantization, prompt_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, json.dumps(list(a.themes)), a.sentiment, int(bool(a.critical_types)),
                 json.dumps(list(a.critical_types)), SAMPLE_MODEL["model"], SAMPLE_MODEL["quantization"],
                 SAMPLE_MODEL["prompt_version"], to_iso(a.submitted_at)),
            )
    data = build_dashboard(conn, dataset, now=GENERATED_AT)
    conn.close()
    return {"sample": True, **data}


def write_fixtures(out: Path = FIXTURES_DIR) -> list[Path]:
    start = datetime(2026, 10, 5, 11, 0, tzinfo=timezone.utc)
    sets = {
        "dashboard-synthetic.json": (fake_answers(480, seed=7, start=start), "synthetic"),
        "dashboard-public.json": (fake_answers(41, seed=11, start=start + timedelta(days=30),
                                               critical_rate=0.08), "public"),
        "dashboard-public-small.json": (fake_answers(3, seed=13, start=start + timedelta(days=34)), "public"),
    }
    written = []
    for name, (answers, dataset) in sets.items():
        path = out / name
        path.write_text(json.dumps(dashboard_for(answers, dataset), ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_fixtures():
        print(written_path)
