import re

import pytest

from enps.classify.lexicon import STRONG, WEAK, Lexicon, default_lexicon, fold, load_lexicon


def hits(text):
    return [(h.type, h.strength) for h in default_lexicon().match(text)]


@pytest.mark.parametrize("text, expected", [
    ("O gerente me assediou depois da reunião", ("am", STRONG)),
    ("Isso aqui é terror psicológico todo dia", ("am", STRONG)),
    ("Sofri assédio sexual do supervisor", ("as", STRONG)),
    ("Ele passou a mão em mim no estoque", ("as", STRONG)),
    ("O encarregado é racista e todo mundo sabe", ("di", STRONG)),
    ("Piada homofóbica toda sexta no grupo", ("di", STRONG)),
    ("Não quero mais viver desse jeito", ("bo", STRONG)),
    ("Às vezes penso que vou me matar", ("bo", STRONG)),
    ("quero sumir pra sempre", ("bo", STRONG)),
    ("O [PESSOA] me bateu no pátio", ("av", STRONG)),
    ("Recebi ameaça de morte de um colega", ("av", STRONG)),
])
def test_strong_signals(text, expected):
    assert expected in hits(text)


@pytest.mark.parametrize("text, expected", [
    ("O coordenador gritou comigo de novo", ("am", WEAK)),
    ("Ela me humilha nas reuniões", ("am", WEAK)),
    ("Ele me expõe na frente da equipe", ("am", WEAK)),
    ("Faz comentário sobre minha roupa todo dia", ("as", WEAK)),
    ("É convite pra sair insistente toda semana", ("as", WEAK)),
    ("Promoção só sai pra quem tem o perfil da vaga", ("di", WEAK)),
    ("Depois da licença maternidade fui deixada de lado", ("di", WEAK)),
    ("Não durmo direito há meses", ("bo", WEAK)),
    ("Tomo remédio pra aguentar o turno", ("bo", WEAK)),
    ("Tive crise de ansiedade no caixa", ("bo", WEAK)),
    ("Choro no banheiro quase todo dia", ("bo", WEAK)),
    ("Ele disse que sabe onde eu moro", ("av", WEAK)),
    ("O cliente me ameaçou na loja", ("av", WEAK)),
])
def test_weak_signals(text, expected):
    assert expected in hits(text)


@pytest.mark.parametrize("text", [
    "Adoro minha equipe e meu gestor é muito justo.",
    "O sistema trava toda hora e o reajuste foi baixo.",
    "Tô me matando de trabalhar, vou me matar de tanto fazer hora extra.",
    "O gestor passa a mão na cabeça de quem erra.",
    "O cliente chora desconto toda hora.",
    "A comida do refeitório é uma delícia.",
    "Queria mais treinamento e um plano de carreira claro.",
    "",
])
def test_ordinary_comments_have_no_hits(text):
    assert hits(text) == []


@pytest.mark.parametrize("text", [
    "Nunca sofri assédio aqui.",
    "Não tem racismo na minha área.",
    "Sem nenhum caso de assédio no meu time.",
    "nao vi nenhum racista por aqui",
])
def test_negated_strong_hit_becomes_weak(text):
    found = default_lexicon().match(text)
    assert found and all(h.strength == WEAK for h in found)
    assert any(h.negated for h in found)


def test_negation_does_not_cross_a_clause_or_the_window():
    assert ("bo", STRONG) in hits("Não aguento mais, vou me matar")
    assert ("am", STRONG) in hits("Não sei explicar direito mas ele me assediou")


@pytest.mark.parametrize("text", [
    "NÃO QUERO MAIS VIVER",
    "nao quero mais viver",
    "n quero mais viver",
    "ñ quero mais viver",
    "naum quero mais viver",
    "Ele é RACISTA",
    "ele e racista",
    "fui assediada",
])
def test_accents_case_and_informal_spellings(text):
    assert any(strength == STRONG for _, strength in hits(text))


def test_hit_offsets_point_to_the_original_text():
    text = "Ontem o Chefe me ASSEDIOU, de novo."
    (hit,) = default_lexicon().match(text)
    assert text[hit.start:hit.end] == hit.text == "ASSEDIOU"
    assert hit.as_dict() == {"type": "am", "strength": STRONG, "text": "ASSEDIOU"}


def test_overlapping_hits_of_one_type_keep_the_strongest():
    found = default_lexicon().match("me assediou")
    assert [(h.type, h.strength) for h in found] == [("am", STRONG)]


def test_fold_keeps_an_index_into_the_original():
    folded, index = fold("Ação É")
    assert folded == "acao e"
    assert index == [0, 1, 2, 3, 4, 5]


def test_lexicon_version_tracks_the_file():
    assert re.fullmatch(r"risk_lexicon\.v1\+[0-9a-f]{8}", default_lexicon().version)
    assert load_lexicon().version == default_lexicon().version


def test_unknown_type_or_macro_is_rejected():
    with pytest.raises(ValueError):
        Lexicon({"version": 1, "types": {"zz": {"strong": ["x"]}}})
    with pytest.raises(ValueError):
        Lexicon({"version": 1, "types": {"am": {"strong": ["{{missing}}"]}}})


@pytest.mark.parametrize("text", [
    "Teve bolinho no café da manhã do aniversário.",
    "O uniforme exige meia-calça nude.",
    "Me agarrei a essa vaga e cresci muito.",
    "Toda segunda me bate uma tristeza.",
    "Me bateu uma saudade da equipe antiga.",
    "Tem uma porrada de pedido atrasado.",
    "Se continuar assim a empresa vai quebrar a cara.",
    "O motorista da rota disse que vai te pegar às seis.",
])
def test_common_words_are_not_strong_hits(text):
    assert all(h.strength != STRONG for h in default_lexicon().match(text))


@pytest.mark.parametrize("text", [
    "Espero que a empresa faça alguma coisa.",
    "Pedi socorro pro TI e ninguém veio.",
    "A empresa tem investido em treinamento e o investidor gostou.",
    "As mudanças não surtiram efeito.",
    "A gravidade do problema ninguém vê.",
    "Faz tudo depressa e mal feito.",
    "O exaustor do galpão não funciona.",
    "Foi uma solução tapa-buraco.",
    "A intimidade da equipe é ótima.",
    "Quero acabar com tudo que é retrabalho.",
])
def test_words_that_only_look_like_signals_have_no_hits(text):
    assert hits(text) == []


@pytest.mark.parametrize("text, expected", [
    ("Ele me bateu uma vez no estoque", ("av", STRONG)),
    ("Ele me agarrou no corredor", ("as", STRONG)),
    ("Ele tentou me agarrar na saída", ("as", STRONG)),
    ("Ele pediu nudes", ("as", STRONG)),
    ("Mandou um nude pra mim", ("as", STRONG)),
    ("Fui bolinada no ônibus da empresa", ("as", STRONG)),
    ("Levei porrada no pátio", ("av", STRONG)),
    ("Disse que ia quebrar a minha cara", ("av", STRONG)),
    ("Apareceu com uma faca", ("av", WEAK)),
    ("Disse que vai te pegar lá fora", ("av", WEAK)),
    ("Não sei mais, penso em acabar com tudo", ("bo", STRONG)),
])
def test_narrowed_patterns_still_catch_the_real_signal(text, expected):
    assert expected in hits(text)
