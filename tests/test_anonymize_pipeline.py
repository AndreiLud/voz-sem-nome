import random
import time

import pytest

from enps.anonymize import PII_TYPES, AnonymizationResult, PiiSpan, anonymize
from enps.anonymize import ner, pipeline


@pytest.fixture(scope="module", autouse=True)
def warm_model():
    ner.load_model()


def masked(text: str) -> list[tuple[str, str]]:
    return [(s.type, text[s.start:s.end]) for s in anonymize(text).spans]


# ---------------------------------------------------------------- contract


def test_contract_example():
    result = anonymize("falei com o João, meu cpf é 123.456.789-00")
    assert isinstance(result, AnonymizationResult)
    assert result.text == "falei com o [PESSOA], meu cpf é [CPF]"
    assert result.counts == {"CPF": 1, "PESSOA": 1}
    assert result.version == pipeline.ANONYMIZER_VERSION == "1.0.0"


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_text(text):
    result = anonymize(text)
    assert result.text == text and result.spans == () and result.counts == {}
    assert result.version == pipeline.ANONYMIZER_VERSION


def test_spans_use_original_coordinates_and_rebuild_the_output():
    text = ("Meu gestor Paulo Mendes (tel 11 98765-4321) mora na Rua Augusta, 1200 e "
            "a placa do carro é ABC1D23. Email paulo.m@talvana.com.br")
    result = anonymize(text)
    spans = result.spans
    assert all(isinstance(s, PiiSpan) and s.type in PII_TYPES for s in spans)
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
    assert [text[s.start:s.end] for s in spans] == [
        "Paulo Mendes", "11 98765-4321", "Rua Augusta, 1200", "ABC1D23", "paulo.m@talvana.com.br"]
    assert pipeline.replace_spans(text, list(spans)) == result.text
    assert sum(result.counts.values()) == len(spans)


def test_anonymize_many_matches_single_calls():
    texts = ["o joao saiu", "", "cpf 123.456.789-00", "Nada a declarar."]
    assert pipeline.anonymize_many(texts) == [anonymize(t) for t in texts]


# ---------------------------------------------------------------- must not mask


@pytest.mark.parametrize("text", [
    "O RH não responde e a TI demora.",
    "A Talvana Distribuição paga em dia.",
    "Trabalho em Operações, mas já passei por Transporte, Atendimento e Pessoas e Cultura.",
    "O Jurídico e o Financeiro brigam com o Comercial e a Tecnologia.",
    "Home office duas vezes por semana seria ótimo.",
    "A PLR caiu e o 13º atrasou.",
    "Recebo R$ 2.500,00 na escala 6x1.",
    "Dou nota 10 para a equipe.",
    "Tenho 5 anos de casa.",
    "Toda segunda-feira às 18h tem reunião.",
    "O pedido 4471 ficou parado no SAP.",
    "O Excel trava e o Teams cai.",
    "A camisa rosa do uniforme é feia.",
    "Falta comunicação clara da diretoria.",
    "A luz do galpão vive queimada.",
    "Aumento de 15% não cobre a inflação de 7,5%.",
    "No dia 10/11 teve treinamento.",
    "Excelente empresa! Recomendo.",
    "Valeu a pena entrar aqui.",
    "Moro longe e o fretado atrasa.",
    "Luz do galpão queimou e ninguém troca.",
    "CLT, VR, VA, FGTS e INSS em dia. A URA e o CRM são lentos.",
    "Gostaria que a Diretoria olhasse mais pra Operações.",
    "O Gerente Regional não ouve ninguém.",
    "Graças a Deus tenho emprego.",
    "Contrataram a irmã da coordenadora.",
    "Março foi puxado, a PLR sai em abril.",
])
def test_ordinary_workplace_text_is_kept(text):
    assert masked(text) == []
    assert anonymize(text).text == text


# ---------------------------------------------------------------- must mask


@pytest.mark.parametrize("text, expected", [
    ("Paulo grita com todo mundo.", [("PESSOA", "Paulo")]),
    ("Meu supervisor, o Ricardo, vive me expondo.", [("PESSOA", "Ricardo")]),
    ("falei com o joao e a maria", [("PESSOA", "joao"), ("PESSOA", "maria")]),
    ("A Clara do Financeiro me ajudou.", [("PESSOA", "Clara")]),
    ("Falei com Clara ontem.", [("PESSOA", "Clara")]),
    ("Rosa me ajuda sempre, já o Marcos some.", [("PESSOA", "Rosa"), ("PESSOA", "Marcos")]),
    ("Quero ser promovido mas o Sr. Antunes não deixa.", [("PESSOA", "Antunes")]),
    ("o Seu Zé da portaria é gente boa", [("PESSOA", "Zé")]),
    ("a dona Socorro do RH some", [("PESSOA", "Socorro")]),
    ("Bruno e Fernanda são ótimos líderes.", [("PESSOA", "Bruno"), ("PESSOA", "Fernanda")]),
    ("Obrigado, Juliana!", [("PESSOA", "Juliana")]),
    ("o joao da silva sumiu", [("PESSOA", "joao da silva")]),
    ("me marcou @mariana_s no grupo", [("PESSOA", "@mariana_s")]),
    ("O SUPERVISOR CARLOS GRITA COM TODO MUNDO NA FRENTE DOS CLIENTES",
     [("PESSOA", "CARLOS")]),
    ("pergunta pro jefferson, ele sabe", [("PESSOA", "jefferson")]),
    ("o gerente paulinho e a bia da expedição", [("PESSOA", "paulinho"), ("PESSOA", "bia")]),
    ("o gerente Marco Sales some", [("PESSOA", "Marco Sales")]),
])
def test_people(text, expected):
    assert masked(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("O Galpão 3 em Guarulhos não tem ventilação.", [("LOCAL", "Guarulhos")]),
    ("Moro no Jardim Ângela e trabalho em São Paulo.",
     [("LOCAL", "Jardim Ângela"), ("LOCAL", "São Paulo")]),
    ("Moro na Rua Maria Antônia, 300.", [("LOCAL", "Rua Maria Antônia, 300")]),
])
def test_places(text, expected):
    assert masked(text) == expected


@pytest.mark.parametrize("text, pii_type", [
    ("meu cpf 529.982.247-25", "CPF"),
    ("cnpj 12.ABC.345/01DE-35", "CNPJ"),
    ("rg 12.345.678-9", "RG"),
    ("cep 01310-100", "CEP"),
    ("me liga (11) 98765-4321", "TELEFONE"),
    ("escreve pra joao@talvana.com.br", "EMAIL"),
    ("matrícula 45821", "MATRICULA"),
    ("placa ABC-1234", "PLACA"),
    ("protocolo 12345678", "DOCUMENTO"),
])
def test_each_regex_type_reaches_the_output(text, pii_type):
    result = anonymize(text)
    assert result.counts == {pii_type: 1}
    assert f"[{pii_type}]" in result.text


# ---------------------------------------------------------------- merge rules


def test_regex_wins_over_names():
    text = "Mande pra ana.paula@talvana.com.br ou fale com a Ana Paula."
    assert masked(text) == [("EMAIL", "ana.paula@talvana.com.br"), ("PESSOA", "Ana Paula")]


def test_join_name_spans():
    gaz = [PiiSpan(4, 9, "PESSOA", "gazetteer")]
    joined = pipeline.join_name_spans(gaz, [PiiSpan(0, 9, "LOCAL", "ner")])
    assert joined == [PiiSpan(0, 9, "LOCAL", "ner")]  # a place that contains a name
    joined = pipeline.join_name_spans(gaz, [PiiSpan(4, 9, "LOCAL", "ner")])
    assert joined == [PiiSpan(4, 9, "PESSOA", "gazetteer")]  # same word: the name wins
    joined = pipeline.join_name_spans(gaz, [PiiSpan(4, 16, "PESSOA", "ner")])
    assert joined == [PiiSpan(4, 16, "PESSOA", "gazetteer")]


def test_merge_cuts_name_spans_around_regex_spans():
    text = "Paulo 123.456.789-00 Mendes"
    regex = [PiiSpan(6, 20, "CPF", "regex")]
    names = [PiiSpan(0, 27, "PESSOA", "ner")]
    merged = pipeline.merge_spans(text, regex, names)
    assert [(s.type, text[s.start:s.end]) for s in merged] == [
        ("PESSOA", "Paulo"), ("CPF", "123.456.789-00"), ("PESSOA", "Mendes")]


def test_mixed_comment():
    text = ("A coordenadora Fernanda Lima me humilhou na frente do time. "
            "Já falei com o RH, meu whats é 11 98765-4321 e minha matrícula 45821.")
    result = anonymize(text)
    assert result.text == (
        "A coordenadora [PESSOA] me humilhou na frente do time. "
        "Já falei com o RH, meu whats é [TELEFONE] e minha matrícula [MATRICULA].")


# ---------------------------------------------------------------- performance


def test_400_comments_under_5_seconds():
    base = [
        "Meu gestor Paulo Mendes grita com a equipe na frente de todos.",
        "O RH não responde, a PLR atrasou e o sistema SAP cai toda hora.",
        "falei com o joao, meu cpf é 123.456.789-00 e o whats 11 98765-4321",
        "Gosto muito da equipe, o home office ajuda e a liderança escuta.",
        "Moro na Rua Augusta, 1200 e o fretado da Talvana atrasa.",
    ]
    texts = [f"{t} Comentário {i}." for i, t in enumerate(base * 80)]
    start = time.perf_counter()
    for text in texts:
        anonymize(text)
    assert time.perf_counter() - start < 5.0


def test_invariants_on_random_mixtures():
    fragments = [
        "o joao", "Maria das Graças", "cpf 123.456.789-00", "(11) 98765-4321", "RH", "Talvana",
        "Rua Augusta, 1200", "placa ABC1D23", "a@b.com", "13º", "R$ 2.500,00", "Clara", "rosa",
        "matrícula 45821", "cep 01310-100", "São Paulo", "@ana", "12345678", ",", ".", "\n", "!",
        "não aguento mais", "SAP", "segunda-feira", "Seu Zé", "MG-12.345.678", "rg",
    ]
    rng = random.Random(7)
    for _ in range(200):
        text = " ".join(rng.choice(fragments) for _ in range(rng.randint(1, 12)))
        result = anonymize(text)
        spans = result.spans
        assert all(0 <= s.start < s.end <= len(text) for s in spans)
        assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
        assert pipeline.replace_spans(text, list(spans)) == result.text
        assert sum(result.counts.values()) == len(spans)
