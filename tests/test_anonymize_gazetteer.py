import pytest

from enps.anonymize import gazetteer
from enps.anonymize.lexicon import build_lexicon, lexicon, normalize


def names(text: str) -> list[str]:
    return [text[s.start:s.end] for s in gazetteer.find_names(text)]


def test_lexicon_loads_ibge_lists_and_settings_terms():
    lex = lexicon()
    assert len(lex.first_names) >= 3000 and len(lex.surnames) >= 1500
    assert "joao" in lex.first_names and "silva" in lex.surnames
    assert "rosa" in lex.stoplist and "clara" in lex.stoplist
    assert lex.is_allowed("talvana") and lex.is_allowed("operacoes") and lex.is_allowed("rh")
    assert lex.is_allowed("pessoas e cultura") and lex.is_allowed("home office")
    assert normalize("Vitória") == "vitoria"


def test_build_lexicon_with_custom_lists():
    lex = build_lexicon(
        {"allowlist": ["Acme Corp"], "name_cues": ["o", "sr."], "connectors": ["da"],
         "nicknames": ["Tião"]},
        {"survey": {"company": "Acme", "areas": [{"code": "ops", "label": {"pt": "Operações"}}]}},
        first_names=frozenset({"ana", "paulo"}),
        surnames=frozenset({"silva"}),
        stoplist=frozenset(),
    )
    assert lex.is_allowed("acme") and lex.is_allowed("acme corp") and lex.is_allowed("ops")
    assert lex.is_first_name("tiao") and lex.is_first_name("paulinho")
    assert "sr" in lex.abbreviation_cues and "o" not in lex.abbreviation_cues


def test_allowlist_word_that_is_a_name_without_accents_keeps_its_accents():
    lex = build_lexicon({"allowlist": ["Março", "Sales"]}, {},
                        first_names=frozenset({"marco"}), surnames=frozenset({"sales"}),
                        stoplist=frozenset())
    assert lex.is_allowed("Março") and lex.is_allowed("MARÇO") and lex.is_allowed("sales")
    assert not lex.is_allowed("Marco") and not lex.is_allowed("marco")


@pytest.mark.parametrize("text, expected", [
    ("o Marco grita comigo", ["Marco"]),
    ("falei com o marco ontem", ["marco"]),
    ("Março foi puxado, em março tem PLR", []),
    ("A Carla Sales é ótima", ["Carla Sales"]),
    ("o Paulo Vale grita", ["Paulo Vale"]),
    ("o Pedro da Frota sumiu", ["Pedro"]),
    ("a Ana vale ouro", ["Ana"]),
])
def test_names_that_collide_with_allowlist_words(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("falei com o joao ontem", ["joao"]),
    ("a maria sumiu", ["maria"]),
    ("reclamei pro carlos e pra fernanda", ["carlos", "fernanda"]),
    ("minha gestora ana grita", ["ana"]),
    ("o lider bruno e o supervisor marcos", ["bruno", "marcos"]),
    ("seu joaquim e dona francisca da limpeza", ["joaquim", "francisca"]),
    ("O JOÃO ME HUMILHA", ["JOÃO"]),
])
def test_lowercase_names_after_cues(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Paulo grita comigo.", ["Paulo"]),
    ("Fiquei sabendo que Juliana saiu.", ["Juliana"]),
    ("Bom dia. Marcos é ótimo", ["Marcos"]),
    ("(Patrícia) me ajudou", ["Patrícia"]),
    ("Valeu, Ricardo!", ["Ricardo"]),
    ("\"Fernanda\" disse", ["Fernanda"]),
])
def test_capitalized_names_anywhere(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Meu gestor Paulo Mendes grita", ["Paulo Mendes"]),
    ("a Maria das Graças Souza saiu", ["Maria das Graças Souza"]),
    ("o joao da silva falou", ["joao da silva"]),
    ("a Ana Paula Oliveira chegou", ["Ana Paula Oliveira"]),
    ("a Ana-Clara chegou", ["Ana-Clara"]),
    ("a Ana do Financeiro ajudou", ["Ana"]),
    ("o Pedro da logística sumiu", ["Pedro"]),
    ("o joao costa largo", ["joao"]),
])
def test_names_extend_over_surnames(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("a Clara me ajudou", ["Clara"]),
    ("a supervisora Rosa é ótima", ["Rosa"]),
    ("a dona Socorro da limpeza", ["Socorro"]),
    ("comunicação clara faz falta", []),
    ("a camisa rosa é feia", []),
    ("a luz do galpão queimou", []),
    ("a clara do ovo", []),
    ("Clara falta de respeito", []),
    ("a ida pro trabalho cansa", []),
    ("a norma da empresa", []),
    ("o junior da equipe", []),
    ("a irmã do gerente foi promovida", []),
])
def test_stoplist_needs_cue_and_capital(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("o gerente paulinho", ["paulinho"]),
    ("a bia da expedição", ["bia"]),
    ("o Seu Zé da portaria", ["Zé"]),
    ("o Sr. Antunes e a Sra. Lúcia", ["Lúcia"]),
])
def test_nicknames_diminutives_and_titles(text, expected):
    assert names(text) == expected


def test_mentions_are_people():
    text = "me marcou @joao.silva e @mariana_s, escreve pra rh@talvana.com"
    spans = gazetteer.find_names(text)
    assert [text[s.start:s.end] for s in spans] == ["@joao.silva", "@mariana_s"]
    assert {s.source for s in spans} == {"context"}


@pytest.mark.parametrize("text", [
    "O RH e a TI não respondem, a Talvana paga mal.",
    "Trabalho em Operações, a Diretoria sumiu, segunda-feira tem reunião.",
    "O SAP, o Excel e o Teams travam. Home office e PLR.",
    "Falta caminho, cozinha limpa e carinho da chefia.",
])
def test_workplace_words_are_not_names(text):
    assert names(text) == []
