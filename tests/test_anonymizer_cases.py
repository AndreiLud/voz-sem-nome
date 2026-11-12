"""Shared anonymizer cases, run against the Python anonymizer.

tests/fixtures/anonymizer_cases.json is also run against the Worker port of the anonymizer by
worker/test/anonymize.test.js, so both implementations answer the same cases. Cases marked
python_only need the spaCy NER layer, which the Worker does not have; they still run here.
"""
import json
import sys
from pathlib import Path

import pytest

from enps.anonymize import PII_TYPES, anonymize
from enps.anonymize.pipeline import replace_spans

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_worker_data  # noqa: E402

CASES = json.loads((ROOT / "tests" / "fixtures" / "anonymizer_cases.json").read_text(encoding="utf-8"))


def _id(case: dict) -> str:
    return case["text"][:60] or "<empty>"


def _check_invariants(text: str, result) -> None:
    spans = result.spans
    assert all(0 <= s.start < s.end <= len(text) for s in spans)
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
    assert replace_spans(text, list(spans)) == result.text
    assert sum(result.counts.values()) == len(spans)


@pytest.mark.parametrize("case", CASES["must_mask"], ids=_id)
def test_must_mask(case):
    text = case["text"]
    result = anonymize(text)
    assert [[s.type, text[s.start:s.end]] for s in result.spans] == case["spans"]
    if "output" in case:
        assert result.text == case["output"]
    _check_invariants(text, result)


@pytest.mark.parametrize("case", CASES["must_not_mask"], ids=_id)
def test_must_not_mask(case):
    result = anonymize(case["text"])
    assert result.spans == () and result.text == case["text"]


def test_fixture_is_well_formed():
    texts = [c["text"] for c in CASES["must_mask"] + CASES["must_not_mask"]]
    assert len(texts) == len(set(texts)), "duplicate case"
    for case in CASES["must_mask"]:
        assert case["spans"], case["text"]
        for pii_type, value in case["spans"]:
            assert pii_type in PII_TYPES and value in case["text"]
        assert (ROOT / case["from"].split("::")[0]).exists()
    python_only = [c for c in CASES["must_mask"] if c.get("python_only")]
    assert len(python_only) <= 5, "keep the cases the Worker cannot answer few"


def test_worker_data_is_current():
    on_disk = build_worker_data.OUT.read_text(encoding="utf-8")
    assert on_disk == build_worker_data.render(), (
        "worker/anonymize/data.js is stale: run .venv/bin/python scripts/build_worker_data.py")


def test_worker_data_reads_this_checkout_only(monkeypatch, tmp_path):
    """ENPS_ROOT (set in the Docker image) moves the Python lexicon paths, not the build inputs."""
    from enps.anonymize import lexicon as lx

    for name in ("ANONYMIZER_FILE", "STOPLIST_FILE", "FIRST_NAMES_FILE", "SURNAMES_FILE",
                 "MUNICIPALITIES_FILE", "DISTRICTS_FILE", "SUBDISTRICTS_FILE", "PLACE_WORDS_FILE"):
        monkeypatch.setattr(lx, name, tmp_path / "missing")
    assert build_worker_data.render() == build_worker_data.OUT.read_text(encoding="utf-8")


def test_worker_data_matches_the_python_lexicon():
    from enps.anonymize.lexicon import lexicon

    built = build_worker_data.build()
    assert built.first_names == lexicon().first_names and built.surnames == lexicon().surnames
    assert built.allow_tokens == lexicon().allow_tokens and built.name_cues == lexicon().name_cues
    assert built.places == lexicon().places and built.place_cues == lexicon().place_cues
    assert built.place_common == lexicon().place_common and built.place_mixed == lexicon().place_mixed
    assert built.place_stoplist == lexicon().place_stoplist
