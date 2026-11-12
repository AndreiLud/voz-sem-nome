"""Shared classifier cases: every expected value is what the Python classifier returns.

tests/fixtures/classifier_cases.json is also run against the Worker port of the classifier
(worker/classify/) by worker/test/classify.test.js, so both implementations answer the same
cases. This module builds the fixture from case inputs (hand-written edge cases, every
parametrized case of tests/test_classify_*.py, the few-shot examples of the prompts, probes
generated from each lexicon pattern and the anonymized synthetic comments), computes the
expected values with the Python code, and fails when the file on disk differs. It also fails
when worker/classify/data.js is stale.

Rewrite the fixture after changing the classifier, its config or the cases:

    PYTHONPATH=src .venv/bin/python tests/test_classifier_cases.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from re import _constants as sre_c  # type: ignore[attr-defined]
from re import _parser as sre_parse  # type: ignore[attr-defined]
from typing import Any, Callable, Iterable

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "scripts", ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import build_worker_classifier_data as worker_data  # noqa: E402
import test_classify_lexicon as lexicon_tests  # noqa: E402
import test_classify_pipeline as pipeline_tests  # noqa: E402
import test_classify_schema as schema_tests  # noqa: E402
from enps.classify.lexicon import STRONG, WEAK, LexiconHit, default_lexicon, fold  # noqa: E402
from enps.classify.pipeline import classify_comment, decide, needs_verification  # noqa: E402
from enps.classify.prompts import load_prompt, user_message  # noqa: E402
from enps.classify.schema import (  # noqa: E402
    ClassifyOutput, OutputError, VerifyOutput, validate_classify, validate_verify,
)
from enps.llm.ollama import LLMReply  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "classifier_cases.json"
RESPONSES = ROOT / "data" / "synthetic" / "responses.csv"
DESCRIPTION = (
    "Classifier cases shared by the Python classifier (tests/test_classifier_cases.py, which writes "
    "this file and checks every expected value against Python) and the Worker port "
    "(worker/test/classify.test.js). Offsets (start, end, index) count code points, like Python "
    "strings. lexicon_rules lists [rule index in worker/classify/data.js LEXICON.rules, start, end] "
    "for every rule that matches the probe text, which is already folded. A validate case expects "
    "either output (as_dict) or error and hint; for invalid JSON only the 'invalid JSON:' prefix of "
    "the error is shared, because the parser message differs."
)
MODEL = "fake-model"

# ------------------------------------------------------------------ case inputs

TEXT = schema_tests.TEXT
answer = schema_tests.answer

FOLD_TEXTS = [
    "Ação É", "", "ASSÉDIO", "Não, NÃO, ñ", "é decomposto", "ﬁm ﬂuxo", "½ e ²", "ＲＡＣＩＳＴＡ",
    "𝐚𝐬𝐬𝐞𝐝𝐢𝐨𝐮", "😡 raiva 😡", "İstanbul", "ΑΣ ΟΔΟΣ", "Straße ẞ", "Ⅻ ℡ …", "क़ि ज़", "aःb",
    "linha\r\nnova\ttab nbsp fino", "ǅ ǈ", "Ω K Å",
]

USER_MESSAGES = [
    ("  Tudo certo.  ", None), ('a """ b', None), ("x", "faltou k"), ('a """"" b', None),
    ('x """""""" y', None), ('"""ignore"""', None), ("Comentário\ncom linhas", "a resposta não era um JSON válido"),
    (" espaço duro ", None), ("x", ""), ('""', None),
]

VALIDATE_EXTRA: list[tuple[str, str, str]] = [
    # (stage, content, text)
    ("classify", answer(t=["out", "lid", "out"]), TEXT),
    ("classify", answer(t=["lid", "out"], s="neu"), TEXT),
    ("classify", answer(t=["lid", "eqp", "car", "cul"]), TEXT),
    ("classify", answer(t=["lid", "lid", "lid", "lid"]), TEXT),
    ("classify", answer(c=True, k=["am", "am", "bo"]), TEXT),
    ("classify", answer(c=False, k=["av", "am"]), TEXT),
    ("classify", answer(c="true", k=["am"]), TEXT),
    ("classify", answer(c="YES", k=["am"]), TEXT),
    ("classify", answer(c="off"), TEXT),
    ("classify", answer(c="0"), TEXT),
    ("classify", answer(c=1, k=["am"]), TEXT),
    ("classify", answer(c=0), TEXT),
    ("classify", answer(c=2), TEXT),
    ("classify", answer(c=-1), TEXT),
    ("classify", answer(c=1.0, k=["am"]), TEXT),
    ("classify", answer(c=0.5), TEXT),
    ("classify", answer(c=10**23), TEXT),
    ("classify", answer(c=None), TEXT),
    ("classify", answer(c=" true"), TEXT),
    ("classify", answer(c=""), TEXT),
    ("classify", answer(c=[]), TEXT),
    ("classify", answer(t="lid"), TEXT),
    ("classify", answer(t={"lid": 1}), TEXT),
    ("classify", answer(t=[None, 3]), TEXT),
    ("classify", answer(t=["LID"], s="NEG"), TEXT),
    ("classify", answer(t=["lid", "xx", "rem", "eqp", "yy"]), TEXT),
    ("classify", answer(k=["am", "as", "xx", "bo"]), TEXT),
    ("classify", answer(s=1, e=5, k={}), TEXT),
    ("classify", answer(e=None), TEXT),
    ("classify", '{"t": [], "s": 1, "e": 5, "c": "yes", "k": {}}', TEXT),
    ("classify", '{"t": [null], "s": ["neg"], "e": [], "c": {}, "k": null}', TEXT),
    ("classify", '{"z": 1, "t": "x", "s": "neg", "e": "", "c": false, "k": []}', TEXT),
    ("classify", '{"z": 1, "t": ["lid"], "s": "neg", "e": "", "c": false, "k": [], "": 2, "a": 3}', TEXT),
    ("classify", '{"t": ["lid"], "c": false}', TEXT),
    ("classify", '{"t": ["lid"], "s": "neg", "c": false}', TEXT),
    ("classify", "{}", TEXT),
    ("classify", '{"t": ["lid"], "s": "neg", "e": "", "c": false, "k": [], "notes": []}', TEXT),
    ("classify", '{"t": ["lid"], "s": "neg", "e": "", "c": false, "k": [], "t": ["car"]}', TEXT),
    ("classify", '  {"t": ["lid"], "s": "neg", "e": "", "c": false, "k": []}\n', TEXT),
    ("classify", '{"t":["lid"],"s":"neg","c":true,"k":["am"],"e":"gritou \\u0063omigo"}', TEXT),
    ("classify", "", TEXT),
    ("classify", "{", TEXT),
    ("classify", "{'t': ['lid']}", TEXT),
    ("classify", '{"t": ["lid"], "s": "neg", "e": "", "c": true, "k": ["am"]} fim', TEXT),
    ("classify", "﻿{}", TEXT),
    ("classify", "null", TEXT),
    ("classify", '"texto"', TEXT),
    ("classify", "123", TEXT),
    ("classify", "true", TEXT),
    ("classify", "```json\n{}\n```", TEXT),
    # excerpts
    ("classify", answer(e="nao sirvo pra nada", c=True, k=["am"]), TEXT),
    ("classify", answer(e="NÃO SIRVO PRA NADA", c=True, k=["am"]), TEXT),
    ("classify", answer(e="“…disse que eu não sirvo…”", c=True, k=["am"]), TEXT),
    ("classify", answer(e="«gritou»", c=True, k=["am"]), TEXT),
    ("classify", answer(e="'gritou   comigo'", c=True, k=["am"]), TEXT),
    ("classify", answer(e="...", c=True, k=["am"]), TEXT),
    ("classify", answer(e="   ", c=True, k=["am"]), TEXT),
    ("classify", answer(e="gritou comigo,", c=True, k=["am"]), "Ele gritou comigo, na frente de todos."),
    ("classify", answer(e="gritou comigo na frente:", c=True, k=["am"]), "Ele gritou comigo na frente: todos viram."),
    ("classify", answer(e="me bat", c=True, k=["av"]), "Ele me batia no estoque."),
    ("classify", answer(e="bat", c=True, k=["av"]), "Ele me batia no estoque."),
    ("classify", answer(e="me humilha todo dia", c=True, k=["am"]), "Ela me humilha todo\ndia."),
    ("classify", answer(e="gritou comigo", c=True, k=["am"]), "Ele gritou\r\ncomigo."),
    ("classify", answer(e="(de novo) subiu 50%", c=True, k=["bo"]), "A meta (de novo) subiu 50%... [sério]?"),
    ("classify", answer(e="[sério]?", c=True, k=["bo"]), "A meta (de novo) subiu 50%... [sério]?"),
    ("classify", answer(e="a+b $5 ^x {y} |z| \\w", c=True, k=["bo"]), "Calculo a+b $5 ^x {y} |z| \\w todo dia."),
    ("classify", answer(e="STRAẞE", c=True, k=["av"]), "Moro na straße do lado."),
    ("classify", answer(e="ÁGUA FRIA", c=True, k=["bo"]), "Só tem água fria no vestiário."),
    ("classify", answer(e="palavra " * 20, c=True, k=["bo"]), "palavra " * 40),
    ("classify", answer(e="x" * 150, c=True, k=["bo"]), "Texto " + "x" * 150 + " fim"),
    ("classify", answer(e=" ".join(["muito"] * 30), c=True, k=["bo"]), " ".join(["muito"] * 30)),
    ("classify", answer(e="😡" * 60 + " me humilha", c=True, k=["am"]), "Ela " + "😡" * 60 + " me humilha."),
    ("classify", answer(e="😡" * 120, c=True, k=["am"]), "😡" * 120),
    ("classify", answer(e="me humilha", c=True, k=["am"]), "Ela me humilha😡"),
    ("classify", answer(e="me humilh", c=True, k=["am"]), "Ela me humilha muito"),
    ("classify", answer(e="ele me bate", c=True, k=["av"]), "Ele me bate_sempre, ele me bate."),
    ("classify", answer(e="ate o carro", c=True, k=["av"]), "Me seguiu até o carro."),
    ("classify", answer(e="meu líder", c=True, k=["am"]), "MEU LÍDER grita."),
    # Python's IGNORECASE also pairs i and I with the Turkish İ and ı
    ("classify", answer(e="istanbul", c=True, k=["di"]), "Ele zomba de quem veio de İstanbul."),
    ("classify", answer(e="ISTANBUL", c=True, k=["di"]), "Ele zomba de quem veio de Istanbul."),
    ("classify", answer(e="ılık", c=True, k=["di"]), "Ele imita o sotaque: ILIK, ILIK."),
    ("classify", answer(e="İ", c=True, k=["di"]), "Chamou de ı na frente de todos."),
    ("classify", answer(e="me humilha İ", c=True, k=["am"]), "Ele me humilha i ri."),
    ("verify", '{"e": "ISSO", "c": true, "k": ["am"]}', "ıssız lugar, isso machuca"),
    # verifier
    ("verify", '{"e": "", "c": false}', TEXT),
    ("verify", '{"e": 1, "c": false, "k": [], "t": []}', TEXT),
    ("verify", '{"c": "no"}', TEXT),
    ("verify", '{"e": "gritou comigo", "c": "true", "k": ["am", "am"]}', TEXT),
    ("verify", '{"e": "x", "c": true, "k": ["am", "as", "di", "bo"]}', TEXT),
    ("verify", '{"e": "", "c": false, "k": ["xx"]}', TEXT),
    ("verify", '{"e": "", "c": false, "k": [], "t": ["lid"], "s": "neg"}', TEXT),
    ("verify", "[]", TEXT),
    ("verify", "not json", TEXT),
]

LEXICON_EXTRA = [
    # accents, case and spacing
    "ASSÉDIO no setor", "Assédio Sexual no setor", "assédio  sexual", "assédio\nsexual", "assédio sexual",
    "assedio sexualmente", "Racismo é crime", "racista!!!", "ex-racista", "_racista", "racista2", "RacIsTa",
    "ＲＡＣＩＳＴＡ", "𝐚𝐬𝐬𝐞𝐝𝐢𝐨𝐮", "ele me assédio", "😡 ele me assediou 😡", "ﬁquei esgotada ﬁnalmente",
    # informal spellings
    "nn quero mais viver", "naum aguento mais", "ñ aguento mais", "q vontade de sumir", "burnout total",
    "burn out total", "burn-out total", "auto lesao", "autolesão", "vc vai ver, vou te pegar", "tô no meu limite",
    "Ele chamou ela de burra", "me chamou de incompetente", "chamou a [PESSOA] de gostosa",
    # negation
    "nunca sofri assédio", "não sofri assédio, mas meu colega sim", "não, ele me assediou",
    "jamais pensei em suicídio", "sem racismo aqui", "Ninguém nunca foi racista comigo",
    "não é que eu não goste, mas ele é muito racista", "não acho que o que ele faz seja racismo",
    "Não; ele me assediou", "Nao! racista", "sem nenhum assedio sexual", "naum foi racismo",
    # overlapping hits and several types
    "me assediou e me humilhou", "recebi ameaça de morte", "me ameaçou de morte", "ele me bateu e me ameaçou",
    "Assédio, racismo, burnout e ameaça de morte no mesmo mês.",
    # placeholders, digits and punctuation
    "O [PESSOA] me bateu", "medo do [PESSOA]", "vou matar o [PESSOA]", "acordo as 3 da manha", "acordo às 3h",
    "acordo as ٣ da manha", "acordo as ３ da manha", "licença-maternidade", "licenca maternidade",
    "tapa-buraco e tapa na cara", "Ele me bate.\nEla me ameaça.", "\t\n", "x",
    # Python's \w and \s are Unicode-aware: these differ from JavaScript's built-in \w and \s
    "racistaß", "ßracista", "racista\u0663", "Ωracista", "assédio\x1csexual", "assédio\x85sexual",
    "assédio\u1680sexual", "assédio\ufeffsexual", "me \u0663 bateu",
    "Tô me matando de trabalhar", "o gerente é tóxico", "ambiente toxico demais", "fotos íntimas no grupo",
    "pediu nudes", "mostrou a faca", "veio armado", "puxou uma arma pra mim",
    ("O clima aqui é ótimo. " * 30) + "Mas ele me assediou.",
]

PIPELINE_TEXTS = [
    "Não aguento mais, vou me matar", "Não sei explicar direito mas ele me assediou",
    "Ontem o Chefe me ASSEDIOU, de novo.", "me assediou", "Meu supervisor me assediou no depósito.",
    "Não durmo direito.", "O gestor gritou comigo.", "Ando muito mal.", "O sistema é lento.",
    "Meu líder vive dizendo que qualquer um faz meu trabalho e ri de mim.", "Meu líder me humilha.",
    "O sistema de pedidos trava toda manhã.",
]


def _param_values(func: Callable[..., Any], name: str) -> list[Any]:
    """Values of one argument of a @pytest.mark.parametrize test, in order."""
    for mark in getattr(func, "pytestmark", ()):
        if mark.name == "parametrize":
            names = [n.strip() for n in mark.args[0].split(",")]
            if name in names:
                idx = names.index(name)
                return [row[idx] if len(names) > 1 else row for row in mark.args[1]]
    raise LookupError(f"{func.__name__} has no parameter {name!r}")


def lexicon_texts() -> list[str]:
    """Texts of tests/test_classify_lexicon.py, the pipeline tests and the edge cases."""
    texts: list[str] = []
    for test in (lexicon_tests.test_strong_signals, lexicon_tests.test_weak_signals,
                 lexicon_tests.test_ordinary_comments_have_no_hits,
                 lexicon_tests.test_negated_strong_hit_becomes_weak,
                 lexicon_tests.test_accents_case_and_informal_spellings,
                 lexicon_tests.test_common_words_are_not_strong_hits,
                 lexicon_tests.test_words_that_only_look_like_signals_have_no_hits,
                 lexicon_tests.test_narrowed_patterns_still_catch_the_real_signal):
        texts += _param_values(test, "text")
    return _unique(texts + PIPELINE_TEXTS + LEXICON_EXTRA)


def validate_inputs() -> list[tuple[str, str, str]]:
    """Answers of tests/test_classify_schema.py, the prompt examples and the edge cases."""
    cases = [("classify", c, TEXT) for c in _param_values(schema_tests.test_malformed_answers_raise, "content")]
    cases += [
        ("classify", answer(t=["lid", "eqp"], e="gritou comigo", c=True, k=["am"]), TEXT),
        ("classify", answer(k=["am"]), TEXT),
        ("classify", answer(c=True, k=[]), TEXT),
        ("classify", answer(t=["out", "lid"]), TEXT),
        ("classify", answer(t=["out"], s="neu"), "Nada."),
        ("classify", answer(e="GRITOU COMIGO na  frente", c=True, k=["am"]), TEXT),
        ("classify", answer(e='"...disse que eu não sirvo pra nada..."', c=True, k=["am"]), TEXT),
        ("classify", answer(e="me xingou de burro", c=True, k=["am"]), TEXT),
        ("classify", answer(e=("palavra " * 40).strip(), c=True, k=["bo"]), "palavra " * 40),
        ("classify", answer(e="gritou comigo na fre", c=True, k=["am"]), TEXT),
        ("classify", answer(t=["lid", "lid"], c=True, k=["am", "am"]), TEXT),
        ("verify", '{"e": "gritou comigo", "c": true, "k": ["am"]}', TEXT),
        ("verify", '{"e": "", "c": false, "k": ["bo"]}', TEXT),
        ("verify", '{"e": "", "c": true, "k": []}', TEXT),
        ("classify", answer(e="ele me bate", c=True, k=["av"]),
         "Primeiro me bateram no pátio e hoje ele me bate de novo."),
        ("classify", answer(e="me bate", c=True, k=["av"]),
         "Primeiro me bateram no pátio e hoje ele me bate de novo."),
    ]
    example = re.compile(r'^Comentário: """(.*)"""\n(\{.*\})$', re.M)
    for stage, setting in (("classify", "classify_prompt"), ("verify", "verify_prompt")):
        name = worker_data._settings_llm()[setting]
        cases += [(stage, raw, comment) for comment, raw in example.findall(load_prompt(name).system)]
    return _unique(cases + VALIDATE_EXTRA)


def _out1(themes=("inf",), sentiment="neg", critical=False, types=(), excerpt="") -> dict[str, Any]:
    return ClassifyOutput(themes=tuple(themes), sentiment=sentiment, excerpt=excerpt,
                          critical=critical, types=tuple(types)).as_dict()


def _verdict(excerpt="", critical=False, types=()) -> dict[str, Any]:
    return VerifyOutput(excerpt=excerpt, critical=critical, types=tuple(types)).as_dict()


def _hit(ctype: str, strength: str, text: str = "x") -> dict[str, Any]:
    return _hit_dict(LexiconHit(type=ctype, strength=strength, text=text, start=0, end=len(text)))


def gate_inputs() -> list[dict[str, Any]]:
    cases = []
    for s1, hits in zip(_param_values(pipeline_tests.test_gate, "s1"),
                        _param_values(pipeline_tests.test_gate, "hits")):
        cases.append({"stage1": s1.as_dict() if s1 else None, "hits": [_hit_dict(h) for h in hits]})
    cases += [
        {"stage1": None, "hits": [_hit("am", STRONG)]},
        {"stage1": _out1(themes=["bem"], sentiment="neg"), "hits": []},
        {"stage1": _out1(themes=["eqp", "inf"], sentiment="neg"), "hits": []},
        {"stage1": _out1(themes=["car"], sentiment="pos"), "hits": []},
        {"stage1": _out1(themes=["rem", "com", "flx", "rec", "cre"], sentiment="neg"), "hits": []},
        {"stage1": _out1(themes=["rem"], sentiment="neu"), "hits": [_hit("di", STRONG)]},
        {"stage1": _out1(themes=["out"], sentiment="neu", critical=True, types=["bo"]), "hits": []},
    ]
    return cases


def decide_inputs() -> list[dict[str, Any]]:
    weak, strong = _hit("bo", WEAK, "não durmo"), _hit("am", STRONG, "assédio")
    no = _verdict()
    return [
        {"stage1": _out1(), "verdict": no, "hits": [_hit("bo", WEAK)]},
        {"stage1": _out1(critical=True, types=["bo"], excerpt="choro todo dia"),
         "verdict": _verdict("me ameaçou", True, ["av", "am"]), "hits": [_hit("di", STRONG, "racista")]},
        {"stage1": _out1(), "verdict": None, "hits": [weak, strong]},
        {"stage1": _out1(critical=True, types=["am"]), "verdict": None, "hits": [weak]},
        {"stage1": _out1(critical=True, types=["am"], excerpt="trecho do estágio 1"),
         "verdict": _verdict("", True, ["am"]), "hits": []},
        {"stage1": _out1(critical=True, types=["am"], excerpt="trecho do estágio 1"),
         "verdict": _verdict("ignorado", False), "hits": []},
        {"stage1": _out1(), "verdict": None, "hits": [_hit("bo", WEAK, "fraco"), _hit("am", STRONG, "forte")]},
        {"stage1": _out1(), "verdict": no, "hits": [_hit("am", STRONG)]},
        {"stage1": None, "verdict": no, "hits": [_hit("am", STRONG)]},
        {"stage1": _out1(), "verdict": None, "hits": [_hit("am", STRONG)]},
        # edge cases
        {"stage1": None, "verdict": None, "hits": []},
        {"stage1": None, "verdict": None, "hits": [weak]},
        {"stage1": None, "verdict": _verdict("x", True, ["as"]), "hits": [weak]},
        {"stage1": _out1(), "verdict": no, "hits": [strong, weak]},
        {"stage1": _out1(critical=True, types=["av", "bo", "av"]), "verdict": _verdict("", True, ["bo", "di"]),
         "hits": [_hit("am", WEAK, "humilha"), _hit("am", STRONG, "assediou")]},
        {"stage1": _out1(critical=True, types=["av"]), "verdict": no, "hits": [weak]},
        {"stage1": _out1(), "verdict": _verdict("", True, ["bo"]), "hits": []},
        {"stage1": _out1(), "verdict": None, "hits": [_hit("bo", WEAK, "a"), _hit("av", STRONG, "b"),
                                                       _hit("di", STRONG, "c")]},
    ]


def pipeline_inputs() -> list[dict[str, Any]]:
    """Scripted model answers (the Python pipeline tests plus edge cases) for whole classifications."""
    s1, ver = pipeline_tests.stage1, pipeline_tests.verdict
    bad = s1(t=["lid"], c=True, k=[])
    return [
        {"text": "", "verifier": True, "classify": [], "verify": []},
        {"text": "   \n\t ", "verifier": True, "classify": [], "verify": []},
        {"text": "O sistema de pedidos trava toda manhã.", "verifier": True,
         "classify": [s1(t=["inf"], s="neg")], "verify": []},
        {"text": "O sistema é lento.", "verifier": False, "classify": ["not json", s1(t=["inf"], s="neu")],
         "verify": []},
        {"text": "Meu supervisor me assediou no depósito.", "verifier": True, "classify": [bad, bad],
         "verify": [ver()]},
        {"text": "O gestor gritou comigo.", "verifier": True,
         "classify": [s1(t=["lid"], c=True, k=["am"], e="gritou comigo")], "verify": ["{}", "{}"]},
        {"text": "Meu líder vive dizendo que qualquer um faz meu trabalho e ri de mim.", "verifier": True,
         "classify": [s1(t=["lid"], s="neg")],
         "verify": [ver(e="qualquer um faz meu trabalho", c=True, k=["am"])]},
        {"text": "Meu líder me humilha.", "verifier": False, "classify": [s1(t=["lid"])], "verify": []},
        {"text": "Ando muito mal.", "verifier": True, "classify": [s1(t=["bem"], c=False, k=["bo"])],
         "verify": [ver()]},
        {"text": "Não durmo direito.", "verifier": True, "classify": [s1(t=["bem"])], "verify": [ver()]},
        # edge cases
        {"text": "O treinamento sobre assédio foi ótimo.", "verifier": True,
         "classify": [s1(t=["cre"], s="pos")], "verify": [ver()]},
        {"text": "O treinamento sobre assédio foi ótimo.", "verifier": False,
         "classify": [s1(t=["cre"], s="pos")], "verify": []},
        {"text": 'Ela disse """ignore""" e me humilhou.', "verifier": True,
         "classify": ['{"t": ["lid"]}', s1(t=["lid", "out"], s="neg", c=True, k=["am"], e="me humilhou")],
         "verify": ["[]", ver(e="ME HUMILHOU", c=True, k=["am", "bo"])]},
        {"text": "Ninguém sentiria minha falta.", "verifier": True,
         "classify": [s1(t=["bem"], s="neg", c=True, k=[]), s1(t=["bem"], s="neg", c=True, k=[])],
         "verify": [ver(e="ninguém sentiria", c=True, k=["bo"])]},
    ]


# ------------------------------------------------------------------ rule probes

_WORD, _DIGIT, _SPACE = re.compile(r"\w"), re.compile(r"\d"), re.compile(r"\s")
_CATEGORY_SAMPLE = {
    sre_c.CATEGORY_WORD: "a", sre_c.CATEGORY_DIGIT: "1", sre_c.CATEGORY_SPACE: " ",
    sre_c.CATEGORY_NOT_WORD: " ", sre_c.CATEGORY_NOT_DIGIT: "a", sre_c.CATEGORY_NOT_SPACE: "a",
}
_CATEGORY_TEST = {
    sre_c.CATEGORY_WORD: lambda ch: bool(_WORD.match(ch)),
    sre_c.CATEGORY_NOT_WORD: lambda ch: not _WORD.match(ch),
    sre_c.CATEGORY_DIGIT: lambda ch: bool(_DIGIT.match(ch)),
    sre_c.CATEGORY_NOT_DIGIT: lambda ch: not _DIGIT.match(ch),
    sre_c.CATEGORY_SPACE: lambda ch: bool(_SPACE.match(ch)),
    sre_c.CATEGORY_NOT_SPACE: lambda ch: not _SPACE.match(ch),
}


def _in_set(ch: str, items: list) -> bool:
    for op, av in items:
        if (op is sre_c.LITERAL and ch == chr(av)) or (op is sre_c.RANGE and av[0] <= ord(ch) <= av[1]) \
                or (op is sre_c.CATEGORY and _CATEGORY_TEST[av](ch)):
            return True
    return False


def _node_samples(op: Any, av: Any) -> list[str]:
    """Short strings for one parsed regex node: the first one matches it, others are variants."""
    if op is sre_c.LITERAL:
        return [chr(av)]
    if op is sre_c.NOT_LITERAL:
        return ["b" if chr(av) == "a" else "a"]
    if op is sre_c.ANY:
        return ["a"]
    if op is sre_c.IN:
        if av and av[0][0] is sre_c.NEGATE:
            return [next(ch for ch in "ax1 -z" if not _in_set(ch, av[1:]))]
        picks = []
        for item_op, item in av:
            if item_op is sre_c.LITERAL:
                picks.append(chr(item))
            elif item_op is sre_c.RANGE:
                picks += [chr(item[0]), chr(item[1])]
            else:
                picks.append(_CATEGORY_SAMPLE[item])
        return _unique(picks)
    if op is sre_c.BRANCH:
        return [s for alternative in av[1] for s in _samples(alternative)]
    if op is sre_c.SUBPATTERN:
        return _samples(av[3])
    if op in (sre_c.MAX_REPEAT, sre_c.MIN_REPEAT):
        low, high, body = av
        inner = _samples(body)
        counts = [low] + ([low + 1] if high > low else []) + ([high] if low + 1 < high <= 4 else [])
        return [inner[0] * n for n in counts] + [s * max(low, 1) for s in inner[1:]]
    if op is sre_c.AT:
        return [""]
    if op is sre_c.ASSERT_NOT:  # the empty variant passes, the others are near misses
        return [""] + _samples(av[1])[:2]
    if op is sre_c.ASSERT:
        return [""]
    raise ValueError(f"no probe generator for {op}")


def _samples(nodes: Iterable[tuple[Any, Any]]) -> list[str]:
    """Variants of a sequence, changing one node at a time from the first variant of each."""
    results = [""]
    for op, av in nodes:
        options = _node_samples(op, av)
        head = results[0]
        results = [r + options[0] for r in results] + [head + o for o in options[1:]]
    return _unique(results)


def rule_probes() -> list[str]:
    """Probe texts (folded) for every lexicon rule: matches, near misses and a sentence around each."""
    probes: list[str] = []
    for rule in default_lexicon().rules:
        samples = [s for s in _samples(sre_parse.parse(rule.regex.pattern)) if s]
        if not any(rule.regex.search(s) for s in samples):
            raise AssertionError(f"no generated probe matches {rule.regex.pattern!r}")
        probes += samples + [f"x {samples[0]} y", f"{samples[0]}a"]
    return _unique(probes)


# ------------------------------------------------------------------ expected values

def _unique(items: list[Any]) -> list[Any]:
    seen, out = set(), []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _hit_dict(hit: LexiconHit) -> dict[str, Any]:
    return {"type": hit.type, "strength": hit.strength, "text": hit.text, "start": hit.start,
            "end": hit.end, "negated": hit.negated}


def _hit_from(data: dict[str, Any]) -> LexiconHit:
    return LexiconHit(type=data["type"], strength=data["strength"], text=data["text"],
                      start=data.get("start", 0), end=data.get("end", len(data["text"])),
                      negated=data.get("negated", False))


def _stage1_from(data: dict[str, Any] | None) -> ClassifyOutput | None:
    if data is None:
        return None
    return ClassifyOutput(themes=tuple(data["t"]), sentiment=data["s"], excerpt=data["e"],
                          critical=data["c"], types=tuple(data["k"]), notes=tuple(data.get("notes", ())))


def _verdict_from(data: dict[str, Any] | None) -> VerifyOutput | None:
    if data is None:
        return None
    return VerifyOutput(excerpt=data["e"], critical=data["c"], types=tuple(data["k"]),
                        notes=tuple(data.get("notes", ())))


def expect_fold(text: str) -> dict[str, Any]:
    folded, index = fold(text)
    return {"text": text, "folded": folded, "index": index}


def expect_user_message(text: str, hint: str | None) -> dict[str, Any]:
    return {"text": text, "hint": hint, "expected": user_message(text, hint)}


def expect_validate(stage: str, content: str, text: str) -> dict[str, Any]:
    validate = validate_classify if stage == "classify" else validate_verify
    try:
        expected: dict[str, Any] = {"output": validate(content, text).as_dict()}
    except OutputError as exc:
        expected = {"error": str(exc), "hint": exc.hint}
    return {"stage": stage, "content": content, "text": text, "expected": expected}


def expect_lexicon(text: str) -> dict[str, Any]:
    return {"text": text, "hits": [_hit_dict(h) for h in default_lexicon().match(text)]}


def expect_rules(text: str) -> dict[str, Any]:
    matches = [[i, m.start(), m.end()] for i, rule in enumerate(default_lexicon().rules)
               for m in rule.regex.finditer(text) if m.end() > m.start()]
    return {"text": text, "matches": matches}


def expect_gate(case: dict[str, Any]) -> dict[str, Any]:
    hits = [_hit_from(h) for h in case["hits"]]
    return {**case, "expected": needs_verification(_stage1_from(case["stage1"]), hits)}


def expect_decide(case: dict[str, Any]) -> dict[str, Any]:
    hits = [_hit_from(h) for h in case["hits"]]
    decision = decide(_stage1_from(case["stage1"]), _verdict_from(case["verdict"]), hits)
    return {**case, "expected": {"critical": decision.critical, "types": decision.types,
                                 "sources": decision.sources, "evidence": decision.evidence}}


class ScriptedClient:
    """Answers each task from a queue of canned strings and records what was sent."""

    def __init__(self, classify: list[str], verify: list[str]):
        self.queues = {"classify": list(classify), "verify_critical": list(verify)}
        self.calls: list[dict[str, str]] = []

    def chat(self, *, task, system, user, schema, options=None):
        self.calls.append({"task": task, "user": user})
        return LLMReply(content=self.queues[task].pop(0), model=MODEL, prompt_tokens=0, cached_tokens=0,
                        output_tokens=0, total_ms=0, load_ms=0, prompt_ms=0, eval_ms=0)

    def model_info(self):
        return {"model": MODEL}


def expect_pipeline(case: dict[str, Any]) -> dict[str, Any]:
    client = ScriptedClient(case["classify"], case["verify"])
    result = classify_comment(case["text"], client, verifier=case["verifier"])
    stages = [{k: v for k, v in asdict(s).items() if k in ("name", "prompt_version", "ok", "error", "output")}
              for s in result.stages]
    expected = {
        "status": result.status, "themes": result.themes, "sentiment": result.sentiment,
        "critical": result.critical, "critical_types": result.critical_types,
        "critical_sources": result.critical_sources, "evidence": result.evidence,
        "model": result.model, "prompt_version": result.prompt_version,
        "verifier_version": result.verifier_version, "stages": stages, "calls": client.calls,
    }
    return {**case, "expected": expected}


def build_fixture(corpus: list[str]) -> dict[str, Any]:
    return {
        "description": DESCRIPTION,
        "data_version": {"lexicon": default_lexicon().version, "rules": len(default_lexicon().rules)},
        "fold": [expect_fold(t) for t in _unique(FOLD_TEXTS)],
        "user_message": [expect_user_message(t, h) for t, h in USER_MESSAGES],
        "validate": [expect_validate(*case) for case in validate_inputs()],
        "lexicon": [expect_lexicon(t) for t in lexicon_texts()],
        "lexicon_corpus": [expect_lexicon(t) for t in corpus],
        "lexicon_rules": [expect_rules(t) for t in rule_probes()],
        "gate": [expect_gate(c) for c in gate_inputs()],
        "decide": [expect_decide(c) for c in decide_inputs()],
        "pipeline": [expect_pipeline(c) for c in pipeline_inputs()],
    }


def anonymized_corpus() -> list[str]:
    """Every distinct non-empty synthetic comment, anonymized like a live submission."""
    from enps.anonymize import anonymize

    with RESPONSES.open(encoding="utf-8", newline="") as fh:
        comments = [row["comment"] for row in csv.DictReader(fh) if row["comment"].strip()]
    return _unique([anonymize(c).text for c in comments])


def dumps(fixture: dict[str, Any]) -> str:
    """One case per line, so a change shows up as a small diff."""
    lines = ["{"]
    items = list(fixture.items())
    for n, (key, value) in enumerate(items):
        comma = "," if n < len(items) - 1 else ""
        if isinstance(value, list):
            body = ",\n".join(f"    {json.dumps(case, ensure_ascii=False)}" for case in value)
            lines.append(f'  "{key}": [\n{body}\n  ]{comma}')
        else:
            lines.append(f'  "{key}": {json.dumps(value, ensure_ascii=False)}{comma}')
    lines.append("}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ tests

@pytest.fixture(scope="module")
def on_disk() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_worker_data_is_current():
    assert worker_data.OUT.read_text(encoding="utf-8") == worker_data.render(), (
        "worker/classify/data.js is stale: run .venv/bin/python scripts/build_worker_classifier_data.py")


def test_unicode_classes_match_python():
    worker_data.check_unicode_classes()


def test_fixture_is_current(on_disk):
    """Rebuilt from the case inputs (corpus texts from the file) with the Python code, case by case."""
    built = build_fixture([case["text"] for case in on_disk["lexicon_corpus"]])
    assert list(built) == list(on_disk)
    for section, cases in built.items():
        if not isinstance(cases, list):
            assert cases == on_disk[section], section
            continue
        assert len(cases) == len(on_disk[section]), f"{section}: case count changed, rewrite the fixture"
        for expected, stored in zip(cases, on_disk[section]):
            assert stored == expected, f"{section}: stale case, rewrite the fixture"


def test_fixture_covers_every_rule_and_outcome(on_disk):
    rules = len(default_lexicon().rules)
    matched = {m[0] for case in on_disk["lexicon_rules"] for m in case["matches"]}
    assert matched == set(range(rules))
    outcomes = [case["expected"] for case in on_disk["validate"]]
    assert any("output" in o and "notes" in o["output"] for o in outcomes)
    assert any(o.get("error", "").startswith("invalid JSON") for o in outcomes)
    assert any(o.get("error", "").startswith("schema:") for o in outcomes)
    assert any(h["negated"] for case in on_disk["lexicon"] for h in case["hits"])
    assert {case["expected"]["status"] for case in on_disk["pipeline"]} == {"done", "review", "skipped"}


JS_WORD, JS_SPACE = worker_data.WORD, worker_data.SPACE


@pytest.mark.parametrize("pattern, source", [
    (r"(?<!\w)(?:a\w*)(?!\w)", f"(?<!{JS_WORD})(?:a{JS_WORD}*)(?!{JS_WORD})"),
    (r"a\s+b\S\d\D", rf"a{JS_SPACE}+b{worker_data.NOT_SPACE}\p{{Nd}}\P{{Nd}}"),
    (r"[\w\s-]x", rf"[{worker_data.WORD_BODY}{worker_data.SPACE_BODY}-]x"),
    (r"a.b$", r"a[^\n]b(?=\n?$)"),
    (r"\Aab\Z", r"^ab$"),
    (r"x{,3}y{2,}z{2}?", r"x{0,3}y{2,}z{2}?"),
    (r"a{b}]c{}", r"a\{b\}\]c\{\}"),
    (r"[]a][^]b]", r"[\]a][^\]b]"),
    (r"\bok\B", f"{worker_data.BOUNDARY}ok{worker_data.NOT_BOUNDARY}"),
    (r"(?P<x>a)(?#note)\-\ \#", r"(?<x>a)- #"),
    (r"\x41\u00e9\U0001F600\N{EM DASH}\a", r"\x41\xe9\u{1f600}\u2014\x07"),
])
def test_regex_conversion(pattern, source):
    assert worker_data.to_js_regex(pattern) == (source, "u")


@pytest.mark.parametrize("pattern, flags", [
    (r"(?i)a", 0), (r"(?i:a)", 0), (r"(?>a)", 0), (r"a*+", 0), (r"(a)\1", 0), (r"(?P<x>a)(?P=x)", 0),
    (r"(a)?(?(1)b|c)", 0), (r"[\W]", 0), (r"\0", 0), (r"a", re.IGNORECASE),
])
def test_regex_conversion_refuses_what_has_no_equivalent(pattern, flags):
    with pytest.raises(worker_data.RegexConversionError):
        worker_data.to_js_regex(pattern, flags)


@pytest.mark.parametrize("text", ["Ação É", "😡 ele me assediou", "ＲＡＣＩＳＴＡ"])
def test_hit_offsets_are_code_points(text):
    for hit in default_lexicon().match(text):
        assert text[hit.start:hit.end] == hit.text


if __name__ == "__main__":
    FIXTURE.write_text(dumps(build_fixture(anonymized_corpus())), encoding="utf-8")
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(f"wrote {FIXTURE.relative_to(ROOT)}: " + ", ".join(
        f"{k} {len(v)}" for k, v in data.items() if isinstance(v, list)))
