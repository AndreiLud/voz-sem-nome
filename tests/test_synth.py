"""Synthetic survey data: distributions, labels, personal-data gold, splits, reproducibility."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from enps import config
from enps.synth import DEFAULT_SEED, generate, write_dataset
from enps.synth.__main__ import main
from enps.synth.bank import fold, load_bank
from enps.synth.pii import VALID_DDDS, is_valid_cpf
from enps.synth.story import AREA_SIZES, CRITICAL_TYPES, PII_TARGETS, TARGET_ENPS, enps

FILES = ("responses.csv", "intended_labels.csv", "pii_gold.jsonl", "splits.json")


@pytest.fixture(scope="module")
def bank():
    return load_bank()


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory, bank) -> Path:
    out = tmp_path_factory.mktemp("synth")
    write_dataset(generate(DEFAULT_SEED, bank), out)
    return out


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def data(out_dir):
    responses = read_csv(out_dir / "responses.csv")
    labels = {r["id"]: r for r in read_csv(out_dir / "intended_labels.csv")}
    gold = [json.loads(line) for line in (out_dir / "pii_gold.jsonl").read_text(encoding="utf-8").splitlines()]
    splits = json.loads((out_dir / "splits.json").read_text(encoding="utf-8"))
    return responses, labels, gold, splits


def traits(label: dict[str, str]) -> set[str]:
    return set(filter(None, label["traits"].split("|")))


def test_areas_scores_and_tenure_tell_the_story(data):
    responses, *_ = data
    assert Counter(r["area"] for r in responses) == AREA_SIZES
    by_area = {a: [int(r["score"]) for r in responses if r["area"] == a] for a in AREA_SIZES}
    for area, target in TARGET_ENPS.items():
        assert abs(enps(by_area[area]) - target) <= 8, area
    assert -14 <= enps([int(r["score"]) for r in responses]) <= -2
    by_tenure = {t: enps([int(r["score"]) for r in responses if r["tenure"] == t]) for t in config.tenure_codes()}
    assert by_tenure["lt1"] == max(by_tenure.values())
    assert by_tenure["y1to3"] == min(by_tenure.values())


def test_ids_times_and_comment_mix(data):
    responses, labels, *_ = data
    assert [r["id"] for r in responses] == [f"S{i:04d}" for i in range(1, 401)]
    times = [datetime.fromisoformat(r["submitted_at"]) for r in responses]
    assert times == sorted(times)
    for t in times:
        assert t.utcoffset().total_seconds() == -3 * 3600
        assert t.weekday() < 5 and 8 <= t.hour < 19
        assert datetime(2026, 10, 19).date() <= t.date() <= datetime(2026, 10, 30).date()
    empty = [r for r in responses if not r["comment"]]
    short = [r for r in responses if traits(labels[r["id"]]) & {"one_word", "short"}]
    assert 50 <= len(empty) <= 70
    assert 25 <= len(short) <= 40
    assert all(r["comment"] == r["comment"].strip() and "\n" not in r["comment"] for r in responses)
    assert not any(re.search(r"\bpra [ao]s?\b", r["comment"]) for r in responses)
    assert not any({"rant", "formal"} <= traits(labels[r["id"]]) for r in responses)


def test_labels_follow_the_taxonomy(data):
    responses, labels, *_ = data
    themes, sentiments = set(config.theme_codes()), set(config.sentiment_codes())
    for r in responses:
        lab = labels[r["id"]]
        codes = lab["themes"].split("|") if lab["themes"] else []
        if not r["comment"]:
            assert (lab["themes"], lab["sentiment"], lab["critical"], lab["traits"]) == ("", "", "0", "empty")
            continue
        assert codes and set(codes) <= themes
        assert "out" not in codes or codes == ["out"]
        assert lab["sentiment"] in sentiments
        assert (lab["critical"] == "1") == bool(lab["critical_types"])
        assert lab["subtle"] == "0" or lab["critical"] == "1"
    used = Counter(t for lab in labels.values() for t in filter(None, lab["themes"].split("|")))
    assert set(used) == themes
    for rare in ("cul", "rec", "com", "flx"):
        assert used[rare] < used["car"] and used[rare] < used["rem"]


def test_critical_cases(data, bank):
    responses, labels, *_ = data
    comments = {r["id"]: r["comment"] for r in responses}
    critical = [lab for lab in labels.values() if lab["critical"] == "1"]
    by_type = Counter(c for lab in critical for c in lab["critical_types"].split("|"))
    assert dict(by_type) == CRITICAL_TYPES
    assert 30 <= len(critical) <= 33
    subtle = [lab for lab in critical if lab["subtle"] == "1"]
    assert len(subtle) / len(critical) >= 0.6
    for lab in subtle:
        assert not bank.is_explicit(comments[lab["id"]]), comments[lab["id"]]
    self_harm = [lab for lab in labels.values() if "self_harm" in traits(lab)]
    assert len(self_harm) == 2 and all("bo" in lab["critical_types"] for lab in self_harm)
    areas = Counter(r["area"] for r in responses if labels[r["id"]]["critical"] == "1")
    assert areas.most_common(2)[0][0] in ("atendimento", "operacoes")
    assert len(areas) >= 6


def test_near_misses_are_not_critical(data):
    _, labels, *_ = data
    near = [lab for lab in labels.values() if "decoy" in traits(lab)]
    assert len(near) == 12
    assert all(lab["critical"] == "0" for lab in near)


def test_pii_gold_offsets_and_counts(data):
    responses, labels, gold, _ = data
    comments = {r["id"]: r["comment"] for r in responses}
    counts: Counter[str] = Counter()
    for entry in gold:
        comment = comments[entry["id"]]
        previous_end = 0
        for span in entry["spans"]:
            assert comment[span["start"]:span["end"]] == span["text"]
            assert span["start"] >= previous_end
            previous_end = span["end"]
            counts[span["type"]] += 1
    for pii_type, minimum in PII_TARGETS.items():
        assert counts[pii_type] >= minimum, pii_type
    gold_ids = {e["id"] for e in gold}
    assert gold_ids == {i for i, lab in labels.items() if "pii" in traits(lab)}
    assert 0.12 <= len(gold_ids) / len(responses) <= 0.18


def test_planted_values_are_harmless(data):
    _, _, gold, _ = data
    spans = [s for e in gold for s in e["spans"]]
    cpfs = [s["text"] for s in spans if s["type"] == "CPF"]
    assert any("." in c for c in cpfs) and any(c.isdigit() for c in cpfs)
    assert not any(is_valid_cpf(c) for c in cpfs)
    for email in (s["text"] for s in spans if s["type"] == "EMAIL"):
        assert email.split("@")[1] in ("example.com", "example.org", "talvana.example")
    for phone in (s["text"] for s in spans if s["type"] == "TELEFONE"):
        digits = re.sub(r"\D", "", phone)
        assert len(digits) == 11 and int(digits[:2]) in VALID_DDDS and digits[2:4] == "90"
    plates = [s["text"].upper() for s in spans if s["type"] == "PLACA"]
    assert all(re.fullmatch(r"[A-Z]{3}-\d{4}|[A-Z]{3}\d[A-Z]\d{2}", p) for p in plates)
    matriculas = [s["text"] for s in spans if s["type"] == "MATRICULA"]
    assert all(m.isdigit() for m in matriculas)


def test_names_that_are_words_appear_both_ways(data, bank):
    responses, _, gold, _ = data
    word_names = set(bank.people["word_names"])
    as_names = {s["text"].split()[0].capitalize() for e in gold for s in e["spans"]
                if s["type"] == "PESSOA" and s["text"].split()[0].capitalize() in word_names}
    assert as_names
    span_ids = {e["id"] for e in gold}
    as_words = [r for r in responses if r["id"] not in span_ids
                and re.search(r"\b(rosa|clara|vit[oó]ria|luz|gra[cç]a)\b", r["comment"].lower())]
    assert len(as_words) >= 5


def test_numbers_and_name_like_words_outside_spans_are_pii_decoys(data, bank):
    responses, labels, gold, _ = data
    spans = {e["id"]: e["spans"] for e in gold}
    words = "|".join(fold(n) for n in bank.people["word_names"])
    for r in responses:
        plain = list(r["comment"])
        for s in spans.get(r["id"], []):
            plain[s["start"]:s["end"]] = " " * (s["end"] - s["start"])
        text = fold("".join(plain))
        if re.search(r"\d", text) or re.search(rf"(?<!\w)({words})(?!\w)", text):
            assert "pii_decoy" in traits(labels[r["id"]]), r["comment"]


def test_splits(data):
    responses, labels, gold, splits = data
    non_empty = {r["id"] for r in responses if r["comment"]}
    test, dev, quick = set(splits["test"]), set(splits["dev"]), set(splits["dev_quick"])
    assert splits["seed"] == DEFAULT_SEED
    assert len(test) == 100 and not test & dev and test | dev == non_empty
    assert len(quick) == 40 and quick <= dev
    gold_ids = {e["id"] for e in gold}

    def critical_counts(ids: set[str]) -> Counter[str]:
        return Counter(c for i in ids for c in filter(None, labels[i]["critical_types"].split("|")))

    assert sum(labels[i]["critical"] == "1" for i in test) >= 20
    assert all(critical_counts(test)[c] >= 3 for c in config.critical_codes())
    assert sum("decoy" in traits(labels[i]) for i in test) >= 5
    assert len(test & gold_ids) >= 12
    assert sum(bool(traits(labels[i]) & {"one_word", "short"}) for i in test) >= 8
    themes = Counter(t for i in test for t in labels[i]["themes"].split("|"))
    assert all(themes[t] >= 6 for t in config.theme_codes())
    assert sum(labels[i]["critical"] == "1" for i in quick) >= 8
    assert all(critical_counts(quick)[c] >= 1 for c in config.critical_codes())
    assert sum("decoy" in traits(labels[i]) for i in quick) >= 3


def test_same_seed_gives_identical_bytes(out_dir, tmp_path, bank):
    again = tmp_path / "again"
    write_dataset(generate(DEFAULT_SEED, bank), again)
    for name in FILES:
        assert (again / name).read_bytes() == (out_dir / name).read_bytes(), name
    other = tmp_path / "other"
    write_dataset(generate(DEFAULT_SEED + 1, bank), other)
    assert (other / "responses.csv").read_bytes() != (out_dir / "responses.csv").read_bytes()


def test_output_does_not_depend_on_hash_randomization(out_dir, tmp_path):
    env = {**os.environ, "PYTHONHASHSEED": "12345", "PYTHONPATH": str(config.ROOT / "src")}
    subprocess.run([sys.executable, "-m", "enps.synth", "--out", str(tmp_path), "--quiet"],
                   check=True, env=env, cwd=config.ROOT)
    for name in FILES:
        assert (tmp_path / name).read_bytes() == (out_dir / name).read_bytes(), name


@pytest.mark.parametrize("seed", [1, 2, 22])
def test_other_seeds_keep_the_targets(seed, bank):
    ds = generate(seed, bank)
    records = ds.records
    for area, target in TARGET_ENPS.items():
        assert abs(enps([r.respondent.score for r in records if r.respondent.area == area]) - target) <= 8
    by_tenure = {t: enps([r.respondent.score for r in records if r.respondent.tenure == t])
                 for t in config.tenure_codes()}
    assert by_tenure["lt1"] > by_tenure["y1to3"] < by_tenure["gt5"]
    spans = Counter(s["type"] for r in records for s in r.comment.spans)
    assert all(spans[t] >= n for t, n in PII_TARGETS.items())
    assert Counter(c for r in records for c in r.comment.critical_types) == CRITICAL_TYPES


def test_cli_writes_files_and_prints_summary(tmp_path, capsys):
    assert main(["--seed", "5", "--out", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    for word in ("eNPS", "Tenure", "Themes", "Critical", "PII spans", "dev_quick"):
        assert word in printed
    assert all((tmp_path / name).exists() for name in FILES)
    assert json.loads((tmp_path / "splits.json").read_text(encoding="utf-8"))["seed"] == 5
