"""Phrase bank, fake personal data and typing noise used by the synthetic data generator."""
from __future__ import annotations

import random
import re

import pytest
import yaml

from enps import config
from enps.synth.bank import DEFAULT_BANK_PATH, fold, load_bank
from enps.synth.noise import capitalize_sentences, make_typo
from enps.synth.pii import FakeData, cpf_check_digits, is_valid_cpf


@pytest.fixture(scope="module")
def bank():
    return load_bank()


def _plain(text: str) -> str:
    text = re.sub(r"\bpra\b", "para", fold(text))
    return re.sub(r"[^\w ]", "", text).strip()


def test_bank_is_large_and_has_no_duplicates(bank):
    regular = [f.text for f in (*bank.fragments, *bank.rants, *bank.short, *bank.near_miss, *bank.decoys)]
    assert len(set(regular)) == len(regular)
    assert len(set(regular)) >= 350
    assert len(bank.critical) >= 40 and len(bank.pii) >= 80


def test_taxonomy_examples_are_never_copied(bank):
    taxonomy = config.taxonomy()
    examples = [e for t in taxonomy["themes"] for e in t["examples"]]
    examples += [q for c in taxonomy["critical_types"] for q in re.findall(r"'([^']+)'", c["signals"]["pt"])]
    texts = [_plain(f.text) for f in (*bank.fragments, *bank.rants, *bank.short, *bank.near_miss,
                                      *bank.decoys, *bank.critical, *bank.pii)]
    for example in map(_plain, examples):
        assert not any(example in text for text in texts), example


def test_explicit_keywords(bank):
    assert bank.is_explicit("Sofri ASSÉDIO no setor")
    assert bank.is_explicit("ele me ameaçou na saída")
    assert not bank.is_explicit("ele disse que sabe em que rua eu moro")
    for frag in bank.critical:
        assert frag.subtle != bank.is_explicit(frag.text)
    assert sum(f.self_harm for f in bank.critical) >= 2


def _broken_bank(tmp_path, change) -> str:
    raw = yaml.safe_load(DEFAULT_BANK_PATH.read_text(encoding="utf-8"))
    change(raw)
    path = tmp_path / "bank.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return str(path)


def test_loader_rejects_bad_entries(tmp_path):
    def unknown_theme(raw):
        raw["near_miss"][0]["th"] = ["xyz"]

    def out_with_other_theme(raw):
        raw["short"][0]["th"] = ["out", "rem"]

    def subtle_with_keyword(raw):
        explicit = next(c for c in raw["critical"] if not c["subtle"])
        explicit["subtle"] = True

    def keyword_in_regular_fragment(raw):
        raw["fragments"]["lid"]["neg"].append("isso aqui é assédio puro")

    def unknown_slot(raw):
        raw["pii"][0]["t"] = "falei com {NOME}"

    for change in (unknown_theme, out_with_other_theme, subtle_with_keyword,
                   keyword_in_regular_fragment, unknown_slot):
        with pytest.raises(ValueError):
            load_bank(_broken_bank(tmp_path, change))


def test_cpf_helpers_and_fake_cpfs_are_invalid(bank):
    base = [5, 2, 9, 9, 8, 2, 2, 4, 7]
    first, second = cpf_check_digits(base)
    valid = "".join(map(str, base)) + f"{first}{second}"
    assert is_valid_cpf(valid)
    assert not is_valid_cpf("111.111.111-11")
    fake = FakeData(random.Random(3), bank.people, bank.places)
    assert not any(is_valid_cpf(fake.cpf()) for _ in range(500))


def test_fake_values_have_the_expected_shape(bank):
    fake = FakeData(random.Random(4), bank.people, bank.places)
    for _ in range(100):
        assert re.fullmatch(r"\(?\d{2}\)? ?90\d{3}-?\d{4}", fake.phone())
        assert re.fullmatch(r"\d{5}-?\d{3}", fake.cep())
        assert fake.email().endswith(("@example.com", "@example.org", "@talvana.example"))
    for _ in range(20):
        fake.forget_person()
        first, last = (fold(p) for p in fake.person("male", "full", "standard").split()[:2])
        local = fake.email().split("@")[0]
        assert local.startswith(first) or local == f"{first[0]}.{last}"


def test_email_uses_a_first_name_given_alone_in_the_same_template(bank):
    fake = FakeData(random.Random(5), bank.people, bank.places)
    for variant in ("first", "lower"):
        for _ in range(20):
            fake.forget_person()
            first = fold(fake.person("female", variant, "casual")).replace(" ", "")
            local = fake.email().split("@")[0]
            assert local.startswith(first) or local.startswith(f"{first[0]}."), (first, local)


def test_typos_and_sentence_case():
    rng = random.Random(1)
    for _ in range(200):
        typo = make_typo("trabalho", rng)
        assert typo != "trabalho" and typo[0] == "t" and abs(len(typo) - 8) <= 1
    text, start = capitalize_sentences("o b.o. sobrou. de novo? sim", True)
    assert text == "O b.o. sobrou. De novo? Sim"
    assert start is False
