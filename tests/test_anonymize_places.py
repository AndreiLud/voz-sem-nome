import json
import sys
from pathlib import Path

import pytest

from enps.anonymize import PiiSpan, anonymize, gazetteer, places
from enps.anonymize.lexicon import lexicon, place_name_ok

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_ibge_places  # noqa: E402

# Also in tests/fixtures/anonymizer_cases.json, so the Worker answers them too.
MASKED = [
    ("Moro em São José dos Campos e trabalho em Jacareí.",
     [["LOCAL", "São José dos Campos"], ["LOCAL", "Jacareí"]]),
    ("moro em sao jose dos campos faz tempo", [["LOCAL", "sao jose dos campos"]]),
    ("Sou de Mogi das Cruzes e pego dois ônibus.", [["LOCAL", "Mogi das Cruzes"]]),
    ("sou de mogi das cruzes", [["LOCAL", "mogi das cruzes"]]),
    ("Fui transferida de Jundiaí para Campinas.", [["LOCAL", "Jundiaí"], ["LOCAL", "Campinas"]]),
    ("A filial de Ribeirão Preto fechou.", [["LOCAL", "Ribeirão Preto"]]),
    ("MORO EM OSASCO E TRABALHO EM BARUERI", [["LOCAL", "OSASCO"], ["LOCAL", "BARUERI"]]),
    ("Moro em Natal e a festa de Natal foi boa.", [["LOCAL", "Natal"]]),
    ("Moro em Marília e a Marília do RH me ajuda.", [["LOCAL", "Marília"], ["PESSOA", "Marília"]]),
    ("Trabalho com a Vitória e moro em Vitória.", [["PESSOA", "Vitória"], ["LOCAL", "Vitória"]]),
    ("moro no bairro jaçanã", [["LOCAL", "jaçanã"]]),
    ("Campinas é longe de casa.", [["LOCAL", "Campinas"]]),
    ("Moro em Contagem e o fretado atrasa.", [["LOCAL", "Contagem"]]),
    ("Moro perto de Itapevi e o fretado não passa.", [["LOCAL", "Itapevi"]]),
    ("venho de pindamonhangaba todo dia", [["LOCAL", "pindamonhangaba"]]),
    ("A unidade de Feira de Santana bate meta.", [["LOCAL", "Feira de Santana"]]),
    ("Falei com o Barbosa e nada mudou.", [["PESSOA", "Barbosa"]]),
    ("Liguei pra Jundiaí e ninguém atendeu.", [["LOCAL", "Jundiaí"]]),
    ("Santa Rita do Sapucaí tem fábrica nova.", [["LOCAL", "Santa Rita do Sapucaí"]]),
    ("Moro em Bom Jesus e trabalho em Embu-Guaçu.", [["LOCAL", "Bom Jesus"], ["LOCAL", "Embu-Guaçu"]]),
    ("Mande para sao.paulo@talvana.com.br amanhã.", [["EMAIL", "sao.paulo@talvana.com.br"]]),
    ("Moro na Rua Guarulhos, 45 desde 2019.", [["LOCAL", "Rua Guarulhos, 45"]]),
    ("A Vila Mariana é cara e o Tatuapé também.", [["LOCAL", "Vila Mariana"], ["LOCAL", "Tatuapé"]]),
    ("Sou de Pau D'Arco do Piauí.", [["LOCAL", "Pau D'Arco do Piauí"]]),
    ("moro em São João del Rei", [["LOCAL", "São João del Rei"]]),
]

KEPT = [
    "A gente mora longe e o fretado atrasa.",
    "a gente mora longe",
    "vim de carro",
    "Vim de carro e voltei de ônibus.",
    "Tenho esperança de que melhore.",
    "Esperança é a última que morre.",
    "Liberdade para trabalhar é bom.",
    "Socorro, ninguém ajuda aqui.",
    "Palmas para a liderança.",
    "O Cruzeiro ganhou ontem e o Palmeiras perdeu.",
    "Na festa de Natal teve sorteio.",
    "Feliz Natal e Boa Sorte a todos.",
    "Graças a Deus e ao Bom Jesus.",
    "O banco Itaú paga em dia.",
    "Moro em casa alugada perto de tudo.",
    "Sou de humanas e vim de outra empresa.",
    "Fui transferido de setor e de turno.",
    "A unidade de negócio cresceu em geral.",
    "Trabalho em Operações e moro perto de casa.",
    "Já trabalhei em Transporte, em Atendimento e em Tecnologia.",
    "A PLR saiu e o pessoal do Comercial comemorou.",
    "O feriado de Sete de Setembro caiu numa segunda.",
    "Contagem de estoque toda sexta.",
    "Bonito mesmo seria pagar em dia.",
    "Em breve teremos PLR, em dia e em casa.",
    "A gente vem de longe e mora em república.",
    "O Comércio e a Assistência Técnica não conversam.",
]


def found(text: str) -> list[str]:
    return [text[p.start:p.end] for p in places.find_places(text)]


def masked(text: str) -> list[list[str]]:
    return [[s.type, text[s.start:s.end]] for s in anonymize(text).spans]


@pytest.mark.parametrize("text, expected", MASKED)
def test_places_are_masked(text, expected):
    assert masked(text) == expected


@pytest.mark.parametrize("text", KEPT)
def test_common_words_and_cues_alone_are_kept(text):
    assert masked(text) == []


def test_the_shared_fixture_has_these_cases():
    cases = json.loads((ROOT / "tests" / "fixtures" / "anonymizer_cases.json").read_text(encoding="utf-8"))
    shared_masked = {c["text"]: c["spans"] for c in cases["must_mask"]}
    shared_kept = {c["text"] for c in cases["must_not_mask"]}
    assert all(shared_masked.get(text) == spans for text, spans in MASKED)
    assert all(text in shared_kept for text in KEPT)


def test_official_lists_are_loaded():
    lex = lexicon()
    assert len(lex.places) > 9000
    for key in ("sao paulo", "embu das artes", "sao jose dos campos", "jardim angela", "copacabana"):
        assert key in lex.places, key
    for label in ("zona 01", "primeiro subdistrito", "centro", "sede", "quarto distrito"):
        assert label not in lex.places, label
    assert lex.place_stoplist <= lex.places, "every stoplist entry is an official place name"
    assert {"moro em", "sou de", "transferido para", "filial de", "bairro", "em"} <= lex.place_cues
    assert lex.place_index["sao"][0] >= 4 and list(lex.place_index["sao"]) == sorted(lex.place_index["sao"], reverse=True)


@pytest.mark.parametrize("name, ok", [
    ("São José dos Campos", True), ("Pau D'Arco", True), ("Embu-Guaçu", True),
    ("Zona 01", False), ("U.T.P. Central", False), ("RA 02 - Santa Lúcia", False),
    ("Primeiro Subdistrito", False), ("Região Administrativa Hipercentro", False),
    ("Sol Nascente/Pôr do Sol", False), ("PTB", False), ("Centro-Sul", False), ("Sede", False),
])
def test_place_name_filter(name, ok):
    assert place_name_ok(name) is ok


def test_word_classes():
    lex = lexicon()
    assert lex.place_class("saude") == lex.place_class("liberdade") == "common"
    assert lex.place_class("palmas") == "mixed"
    assert lex.place_class("guarulhos") == lex.place_class("campinas") == "proper"


@pytest.mark.parametrize("ranks, spelling, expected", [
    ({"saúde": 10, "Saúde": 100}, "Saúde", "common"),
    ({"palmas": 150, "Palmas": 100}, "Palmas", "mixed"),
    ({"campinas": 3000, "Campinas": 300}, "Campinas", "proper"),
    ({"Pirituba": 300}, "Pirituba", "proper"),
    ({"perus": 300}, "Perus", "common"),
    ({"se": 5, "Se": 50, "Sé": 400, "sé": 9000}, "Sé", "common"),  # judged with "se" too
])
def test_word_class_from_frequency_ranks(ranks, spelling, expected):
    assert fetch_ibge_places.classify_spelling(spelling, ranks)[0] == expected


@pytest.mark.parametrize("text, expected", [
    # common words: never at the start of a sentence, never in lowercase
    ("Contagem é longe", []),
    ("moro em Contagem", ["Contagem"]),
    ("moro em contagem", []),
    # words used about as often in both cases: inside a sentence only
    ("Perus é longe", []),
    ("a linha de Perus atrasa", ["Perus"]),
    # proper names: anywhere when capitalized, after a cue in lowercase
    ("Guarulhos é longe", ["Guarulhos"]),
    ("o pessoal de guarulhos", []),
    ("trabalho em guarulhos", ["guarulhos"]),
    # names made only of common words do not start a sentence and need a cue in lowercase
    ("Campo Grande é longe", []),
    ("o pessoal de Campo Grande", ["Campo Grande"]),
    ("moro em campo grande", []),
    ("moro em belo horizonte", ["belo horizonte"]),
    # stoplist: capital letter and cue
    ("o Natal foi bom", []),
    ("moro em natal", []),
    ("sou de Natal", ["Natal"]),
    ("Feliz Natal", []),
    # a lowercase place that is also a first name or surname needs a capital letter
    ("em marco tem PLR", []),
    ("moro em sousa", []),
    ("moro em Sousa", ["Sousa"]),
    # a name must match fully and on one line; hyphens and apostrophes join its words
    ("moro em São José\ndos Campos", ["São José", "Campos"]),
    ("sou de Embu Guaçu", ["Embu Guaçu"]),
    ("sou de Olho d\u2019Água das Flores", ["Olho d\u2019Água das Flores"]),
    # allowlisted words are never places
    ("trabalho na Matriz", []),
    ("trabalho na Câmara", []),  # stoplist: workplace word
])
def test_find_places(text, expected):
    assert found(text) == expected


def test_cues_must_be_on_the_same_line():
    assert found("moro em\npirituba") == []
    assert found("moro em pirituba") == ["pirituba"]


def _span(text: str, part: str, pii_type: str = "PESSOA", source: str = "gazetteer") -> PiiSpan:
    start = text.index(part)
    return PiiSpan(start, start + len(part), pii_type, source)


@pytest.mark.parametrize("text, names, expected", [
    # a longer name holds the place
    ("a Ana Carolina chegou", ["Ana Carolina"], [["PESSOA", "Ana Carolina"]]),
    # a cue makes it a place, even over a first name
    ("moro em Marília", ["Marília"], [["LOCAL", "Marília"]]),
    # the place holds the name
    ("o pessoal de São José dos Campos", ["José dos Campos"], [["LOCAL", "São José dos Campos"]]),
    # a known first name without a place cue stays a person
    ("a Marília do RH", ["Marília"], [["PESSOA", "Marília"]]),
    # no person span: a place right after a name cue made of known names is a person
    ("falei com o Barbosa", [], [["PESSOA", "Barbosa"]]),
    ("o pessoal de Jundiaí", [], [["LOCAL", "Jundiaí"]]),
])
def test_resolve(text, names, expected):
    spans = places.resolve([_span(text, n) for n in names], places.find_places(text), text)
    assert [[s.type, text[s.start:s.end]] for s in spans] == expected


def test_context_spans_without_known_names_give_way_to_places():
    text = "liguei pra Jundiaí"
    context = _span(text, "Jundiaí", source="context")
    assert [s.type for s in places.resolve([context], places.find_places(text), text)] == ["LOCAL"]
    text = "liguei pra Souza"
    context = _span(text, "Souza", source="context")
    assert [s.type for s in places.resolve([context], places.find_places(text), text)] == ["PESSOA"]


def test_reads_as_name():
    assert places.reads_as_name("Mendes") and places.reads_as_name("Guimarães")
    assert not places.reads_as_name("Jundiaí")
    assert not places.reads_as_name("Vila Mariana")  # both words are IBGE surnames, still a place


def test_a_ner_person_takes_back_an_uncued_place_that_reads_as_a_surname():
    from enps.anonymize import pipeline
    lex = lexicon()
    text = "Mendes me ajudou e moro em Castro."
    found = places.find_places(text, lex)
    spans = places.resolve([], found, text, lex)
    ner_people = [_span(text, "Mendes", source="ner"), _span(text, "Castro", source="ner")]
    out = pipeline.surnames_to_people(spans, ner_people, found, text, lex)
    assert [[s.type, text[s.start:s.end]] for s in out] == [["PESSOA", "Mendes"], ["LOCAL", "Castro"]]
    for text, part in [("Moro perto de Jundiaí.", "Jundiaí"), ("A Vila Mariana é cara.", "Vila Mariana")]:
        found = places.find_places(text, lex)
        spans = places.resolve(gazetteer.find_names(text, lex), found, text, lex)
        assert [s.type for s in spans] == ["LOCAL"]
        assert pipeline.surnames_to_people(spans, [_span(text, part, source="ner")], found, text, lex) == spans


def test_a_ner_span_that_adds_no_word_does_not_join_a_name_and_a_place():
    from enps.anonymize import pipeline
    lex = lexicon()
    text = "Falei com a Natália de Jundiaí e com a Kowalski de Jundiaí."
    spans = [_span(text, "Natália"), PiiSpan(text.index("Jundiaí"), text.index("Jundiaí") + 7, "LOCAL", "gazetteer")]
    second = text.rindex("Jundiaí")
    spans.append(PiiSpan(second, second + 7, "LOCAL", "gazetteer"))
    ner_spans = [_span(text, "Natália de Jundiaí", source="ner"), _span(text, "Kowalski de Jundiaí", source="ner")]
    assert pipeline.ner_spans_to_join(spans, ner_spans, text, lex) == ner_spans[1:]


# Python only: they depend on the spaCy NER layer, which the Worker does not have.
@pytest.mark.parametrize("text, expected", [
    ("O coordenador Deyvison Guimarães some.", [["PESSOA", "Deyvison Guimarães"]]),
    ("O Uelinton Leme é gente boa.", [["PESSOA", "Uelinton Leme"]]),
    ("Castro grita com todo mundo.", [["PESSOA", "Castro"]]),
    ("Moro em Castro e trabalho em Leme.", [["LOCAL", "Castro"], ["LOCAL", "Leme"]]),
    ("Falei com a Natália de Jundiaí", [["PESSOA", "Natália"], ["LOCAL", "Jundiaí"]]),
    ("A Marília mora em Marília.", [["PESSOA", "Marília"], ["LOCAL", "Marília"]]),
])
def test_people_with_place_surnames_stay_people(text, expected):
    assert masked(text) == expected


def test_spans_keep_original_coordinates_and_text():
    text = "Sou de São Paulo, moro em Guarulhos."
    result = anonymize(text)
    assert result.text == "Sou de [LOCAL], moro em [LOCAL]."
    assert result.counts == {"LOCAL": 2}
    assert all(s.source == "gazetteer" for s in result.spans)
