import json

import pytest

from enps.classify.schema import (
    EXCERPT_MAX_CHARS, OutputError, classify_schema, validate_classify, validate_verify,
    verify_schema,
)

TEXT = "O coordenador gritou   comigo na frente de todos e disse que eu não sirvo pra nada."


def answer(**fields):
    base = {"t": ["lid"], "s": "neg", "e": "", "c": False, "k": []}
    base.update(fields)
    return json.dumps(base, ensure_ascii=False)


def test_schema_field_order_and_enums():
    schema = classify_schema()
    assert list(schema["properties"]) == ["t", "s", "c", "k", "e"]  # v2: decide, then quote
    assert schema["required"] == ["t", "s", "c", "k", "e"]
    assert schema["additionalProperties"] is False
    assert "out" in schema["properties"]["t"]["items"]["enum"]
    assert schema["properties"]["t"]["minItems"] == 1 and schema["properties"]["t"]["maxItems"] == 4
    assert schema["properties"]["s"]["enum"] == ["pos", "neu", "neg"]
    assert schema["properties"]["e"]["maxLength"] == EXCERPT_MAX_CHARS
    assert schema["properties"]["k"]["items"]["enum"] == ["am", "as", "di", "bo", "av"]
    assert "title" not in json.dumps(schema)
    assert list(verify_schema()["properties"]) == ["e", "c", "k"]


def test_valid_answer_passes_unchanged():
    out = validate_classify(answer(t=["lid", "eqp"], e="gritou comigo", c=True, k=["am"]), TEXT)
    assert out.themes == ("lid", "eqp") and out.sentiment == "neg"
    assert out.critical is True and out.types == ("am",)
    assert out.excerpt == "gritou   comigo" and out.notes == ()
    assert out.as_dict() == {"t": ["lid", "eqp"], "s": "neg", "e": "gritou   comigo", "c": True, "k": ["am"]}


def test_types_without_flag_are_coerced_to_critical():
    out = validate_classify(answer(k=["am"]), TEXT)
    assert out.critical is True
    assert any("c set to true" in n for n in out.notes)
    assert out.as_dict()["notes"]


def test_flag_without_types_is_an_error():
    with pytest.raises(OutputError) as err:
        validate_classify(answer(c=True, k=[]), TEXT)
    assert "k" in err.value.hint


def test_out_mixed_with_other_themes_is_repaired():
    out = validate_classify(answer(t=["out", "lid"]), TEXT)
    assert out.themes == ("lid",)
    assert any('"out" dropped' in note for note in out.notes)
    assert validate_classify(answer(t=["out"], s="neu"), "Nada.").themes == ("out",)


@pytest.mark.parametrize("content", [
    "not json", "[1, 2]", '{"t": [], "s": "neg", "e": "", "c": false, "k": []}',
    '{"t": ["xyz"], "s": "neg", "e": "", "c": false, "k": []}',
    '{"t": ["lid"], "s": "bad", "e": "", "c": false, "k": []}',
    '{"t": ["lid", "car", "rem", "eqp", "cre"], "s": "neg", "e": "", "c": false, "k": []}',
    '{"t": ["lid"], "s": "neg", "e": "", "c": false, "k": [], "extra": 1}',
])
def test_malformed_answers_raise(content):
    with pytest.raises(OutputError) as err:
        validate_classify(content, TEXT)
    assert err.value.hint


def test_excerpt_matches_after_whitespace_and_case_normalization():
    out = validate_classify(answer(e="GRITOU COMIGO na  frente", c=True, k=["am"]), TEXT)
    assert out.excerpt == "gritou   comigo na frente"  # as written in the comment
    assert out.notes == ()
    out = validate_classify(answer(e='"...disse que eu não sirvo pra nada..."', c=True, k=["am"]), TEXT)
    assert out.excerpt == "disse que eu não sirvo pra nada"


def test_excerpt_not_in_text_is_cleared_with_a_note():
    out = validate_classify(answer(e="me xingou de burro", c=True, k=["am"]), TEXT)
    assert out.excerpt == "" and out.critical is True
    assert any("excerpt" in n for n in out.notes)


def test_long_excerpt_is_cut_at_a_word():
    text = "palavra " * 40
    out = validate_classify(answer(e=text.strip(), c=True, k=["bo"]), text)
    assert 0 < len(out.excerpt) <= EXCERPT_MAX_CHARS
    assert out.excerpt.endswith("palavra")


def test_excerpt_stopped_inside_a_word_drops_the_partial_word():
    out = validate_classify(answer(e="gritou comigo na fre", c=True, k=["am"]), TEXT)
    assert out.excerpt == "gritou   comigo na"
    assert any("inside a word" in n for n in out.notes)


def test_duplicates_are_removed():
    out = validate_classify(answer(t=["lid", "lid"], c=True, k=["am", "am"]), TEXT)
    assert out.themes == ("lid",) and out.types == ("am",)


def test_verify_answers():
    out = validate_verify('{"e": "gritou comigo", "c": true, "k": ["am"]}', TEXT)
    assert out.critical and out.types == ("am",) and out.excerpt == "gritou   comigo"
    assert validate_verify('{"e": "", "c": false, "k": ["bo"]}', TEXT).critical is True
    with pytest.raises(OutputError):
        validate_verify('{"e": "", "c": true, "k": []}', TEXT)


def test_excerpt_prefers_the_occurrence_that_ends_at_a_word():
    text = "Primeiro me bateram no pátio e hoje ele me bate de novo."
    out = validate_classify(answer(e="ele me bate", c=True, k=["av"]), text)
    assert out.excerpt == "ele me bate" and out.notes == ()
    out = validate_classify(answer(e="me bate", c=True, k=["av"]), text)
    assert out.excerpt == "me bate" and out.notes == ()


def test_v1_schema_keeps_the_excerpt_first():
    from enps import config
    from enps.classify.schema import _json_schema, _models
    v1 = _models(tuple(config.theme_codes()), tuple(config.sentiment_codes()),
                 tuple(config.critical_codes()), 1)[0]
    assert list(_json_schema(v1)["properties"]) == ["t", "s", "e", "c", "k"]
