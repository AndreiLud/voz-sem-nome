import pytest

from enps.anonymize import patterns
from enps.anonymize import validators as v


def found(text: str) -> list[tuple[str, str]]:
    return [(s.type, text[s.start:s.end]) for s in patterns.find_all(text)]


# ---------------------------------------------------------------- validators


def test_cpf_check_digits():
    assert v.cpf_is_valid("529.982.247-25")
    assert v.cpf_is_valid("52998224725")
    assert not v.cpf_is_valid("529.982.247-26")
    assert not v.cpf_is_valid("111.111.111-11")
    assert not v.cpf_is_valid("1234")


def test_cnpj_check_digits_numeric_and_alphanumeric():
    assert v.cnpj_is_valid("11.222.333/0001-81")
    assert v.cnpj_is_valid("12.ABC.345/01DE-35")
    assert v.cnpj_is_valid("12abc34501de35")
    assert not v.cnpj_is_valid("11.222.333/0001-80")
    assert not v.cnpj_is_valid("00000000000000")


def test_phone_shapes():
    assert v.looks_like_mobile("11987654321")
    assert not v.looks_like_mobile("10987654321")  # DDD 10 does not exist
    assert v.looks_like_landline_or_old_mobile("1134567890")
    assert not v.looks_like_landline_or_old_mobile("1114567890")


# ---------------------------------------------------------------- positives


@pytest.mark.parametrize("text, value", [
    ("meu cpf é 123.456.789-00 ok", "123.456.789-00"),
    ("cpf 529.982.247-25", "529.982.247-25"),
    ("CPF: 123456789-00", "123456789-00"),
    ("cpf 123 456 789 00 pra cadastro", "123 456 789 00"),
    ("o número 52998224725 aparece", "52998224725"),
    ("cpf 12345678900", "12345678900"),
    ("documento 12345678901 no sistema", "12345678901"),
])
def test_cpf_formats_even_with_wrong_check_digits(text, value):
    assert ("CPF", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("cnpj 11.222.333/0001-81", "11.222.333/0001-81"),
    ("empresa 11.222.333/0001-80 errado", "11.222.333/0001-80"),
    ("novo cnpj 12.ABC.345/01DE-35 em 2026", "12.ABC.345/01DE-35"),
    ("sem pontos 11222333000181", "11222333000181"),
    ("alfanumérico 12ABC34501DE35", "12ABC34501DE35"),
])
def test_cnpj_formats(text, value):
    assert ("CNPJ", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("meu rg é 12.345.678-9", "12.345.678-9"),
    ("RG: MG-12.345.678", "MG-12.345.678"),
    ("identidade 123456789", "123456789"),
    ("Rg nº 4.567.890", "4.567.890"),
    ("RG 12.345.678-X", "12.345.678-X"),
    ("rg mg-12.345.678", "mg-12.345.678"),  # lowercase state prefix
    ("meu rg é sp-12.345.678-9", "sp-12.345.678-9"),
    ("RG mg 12.345.678", "mg 12.345.678"),
    ("meu rg e SP-12.345.678 pra cadastro", "SP-12.345.678"),  # "é" without the accent
])
def test_rg_with_cue(text, value):
    assert ("RG", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("ligue (11) 98765-4321", "(11) 98765-4321"),
    ("+55 11 98765-4321", "+55 11 98765-4321"),
    ("+5511987654321", "+5511987654321"),
    ("fone 11 3456-7890", "11 3456-7890"),
    ("whats 11 98765 4321", "11 98765 4321"),
    ("(21)3456.7890", "(21)3456.7890"),
    ("meu celular 98765-4321", "98765-4321"),
    ("fixo 3456-7890", "3456-7890"),
    ("sem ddd 9 8765-4321", "9 8765-4321"),
    ("número 11987654321", "11987654321"),
    ("liga no 1134567890", "1134567890"),
    ("zap 987654321", "987654321"),
    ("ramal 4521", "4521"),
])
def test_phone_formats(text, value):
    assert ("TELEFONE", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("email joao.silva@talvana.com.br.", "joao.silva@talvana.com.br"),
    ("Ana_Souza+rh@gmail.com", "Ana_Souza+rh@gmail.com"),
])
def test_email(text, value):
    assert ("EMAIL", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("moro no cep 01310-100", "01310-100"),
    ("CEP: 01310100", "01310100"),
    ("endereço 04567-000, perto", "04567-000"),
    ("cep 01.310-100", "01.310-100"),
])
def test_cep(text, value):
    assert ("CEP", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("o carro ABC-1234 bateu", "ABC-1234"),
    ("placa abc1234", "abc1234"),
    ("veículo ABC 1234", "ABC 1234"),
    ("Mercosul BRA2E19", "BRA2E19"),
    ("placa bra2e19.", "bra2e19"),
    ("a placa é xyz 9876", "xyz 9876"),
])
def test_plates(text, value):
    assert ("PLACA", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("matrícula 45821", "45821"),
    ("matricula: 004512", "004512"),
    ("mat. 9981", "9981"),
    ("registro 778899", "778899"),
    ("RE 123456", "123456"),
    ("crachá A1234.", "A1234"),
    ("id funcional 33-221", "33-221"),
    ("chapa 772", "772"),
    ("minha matrícula nº 12345", "12345"),
    ("matrícula funcional 12345", "12345"),
    ("registro funcional: 778899", "778899"),
    ("minha matricula e 45821", "45821"),
])
def test_matricula_with_cue(text, value):
    assert ("MATRICULA", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("moro na Rua Augusta, 1200 perto", "Rua Augusta, 1200"),
    ("Av. Paulista 900", "Av. Paulista 900"),
    ("na avenida Brigadeiro Faria Lima", "avenida Brigadeiro Faria Lima"),
    ("Rua 25 de Março, 45", "Rua 25 de Março, 45"),
    ("na rua das flores, 123", "rua das flores, 123"),
    ("Travessa do Comércio nº 12", "Travessa do Comércio nº 12"),
    ("Rodovia Anhanguera, km 23", "Rodovia Anhanguera, km 23"),
    ("moro no bairro Jardim Ângela", "bairro Jardim Ângela"),
])
def test_street_addresses_are_local(text, value):
    assert ("LOCAL", value) in found(text)


@pytest.mark.parametrize("text, value", [
    ("protocolo 12345678", "12345678"),
    ("número 98.765.432-1 sem cue", "98.765.432-1"),
    ("conta 0012345678901234", "0012345678901234"),
])
def test_leftover_numbers_are_documento(text, value):
    assert ("DOCUMENTO", value) in found(text)


# ---------------------------------------------------------------- negatives


@pytest.mark.parametrize("text", [
    "recebo R$ 2.500,00 por mês",
    "o 13º atrasou",
    "escala 6x1",
    "nota 10",
    "5 anos de casa",
    "pedido 4471",
    "saio às 18h",
    "aumento de 15% e 7,5%",
    "dia 10/11 e 10/11/2025",
    "entre 2019-2021 e 2023 2024",
    "faixa de 5000-6000 reais",
    "ano 2024, ISO 9001 e PLR 2025",
    "Ano 2024 foi difícil",
    "a rua tem 30 buracos",
    "a estrada tá ruim",
    "registro de ponto às 8h",
    "registro 100 horas extras",
    "R$ 12345678 de prejuízo",
    "abc 1234",
    "registro e 100 horas de treinamento",
    "rg e cpf atualizados",
])
def test_ordinary_numbers_are_not_masked(text):
    assert found(text) == []


def test_spans_are_sorted_and_disjoint():
    text = "cpf 123.456.789-00, tel (11) 98765-4321, cep 01310-100, placa ABC1D23, email a@b.com"
    spans = patterns.find_all(text)
    assert [s.type for s in spans] == ["CPF", "TELEFONE", "CEP", "PLACA", "EMAIL"]
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))
    assert all(s.source == "regex" for s in spans)
