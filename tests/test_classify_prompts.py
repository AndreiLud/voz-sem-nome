import json
import re

import pytest

from enps import config
from enps.classify.lexicon import fold
from enps.classify.prompts import load_prompt, parse_prompt, render, split_front_matter, user_message
from enps.classify.schema import validate_classify, validate_verify

EXAMPLE = re.compile(r'^Comentário: """(.*)"""\n(\{.*\})$', re.M)
PROMPTS = {"classify.v1": validate_classify, "verify_critical.v1": validate_verify}


def examples(name):
    return EXAMPLE.findall(load_prompt(name).system)


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_loads_with_front_matter_and_version(name):
    prompt = load_prompt(name)
    assert prompt.version == 1
    assert prompt.task == name.split(".")[0]
    assert re.fullmatch(rf"{re.escape(name)}\+[0-9a-f]{{8}}", prompt.prompt_version)
    assert "{{" not in prompt.system and not prompt.system.startswith("---")


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_renders_every_critical_type(name):
    system = load_prompt(name).system
    for ctype in config.taxonomy()["critical_types"]:
        assert f"- {ctype['code']}: {ctype['label']['pt']}" in system
        assert ctype["signals"]["pt"] in system


def test_classify_prompt_renders_themes_and_sentiments():
    system = load_prompt("classify.v1").system
    for code in config.theme_codes() + config.sentiment_codes():
        assert f"- {code}: " in system
    assert "Na dúvida, marque c=true" in system


@pytest.mark.parametrize("name", PROMPTS)
def test_few_shot_examples_follow_the_rules(name):
    found = examples(name)
    assert 8 <= len(found) <= 12
    validate = PROMPTS[name]
    flagged = set()
    for comment, raw in found:
        out = validate(raw, comment)
        assert out.notes == (), (comment, out.notes)
        assert list(json.loads(raw)) == list(out.as_dict())  # field order as in the schema
        if out.critical:
            assert out.excerpt
            flagged.update(out.types)
    assert flagged == set(config.critical_codes())
    assert any(not validate(raw, comment).critical for comment, raw in found)


def test_examples_are_not_copied_from_the_taxonomy():
    taxonomy_examples = {e for t in config.taxonomy()["themes"] for e in t.get("examples", [])}
    for name in PROMPTS:
        assert not {comment for comment, _ in examples(name)} & taxonomy_examples


def test_classify_examples_cover_out_irony_and_one_word():
    found = examples("classify.v1")
    outputs = [validate_classify(raw, comment) for comment, raw in found]
    assert any(o.themes == ("out",) for o in outputs)
    assert any(len(comment.split()) == 1 for comment, _ in found)
    assert {o.sentiment for o in outputs} == {"pos", "neu", "neg"}


@pytest.mark.parametrize("name", PROMPTS)
def test_prompt_text_has_no_long_dashes(name):
    system = load_prompt(name).system
    assert "\u2014" not in system and "\u2013" not in system and " - " not in system


def test_version_changes_when_the_rendered_text_changes():
    taxonomy = config.taxonomy()
    source = "---\nversion: 1\ntask: classify\ndescription: x\n---\nA\n{{sentiments}}\n"
    first = parse_prompt("t.v1", source, taxonomy)
    second = parse_prompt("t.v1", source.replace("A\n", "B\n"), taxonomy)
    assert first.prompt_version != second.prompt_version
    assert first.prompt_version == parse_prompt("t.v1", source, taxonomy).prompt_version


def test_bad_prompt_files_are_rejected():
    with pytest.raises(ValueError):
        split_front_matter("no front matter")
    with pytest.raises(ValueError):
        split_front_matter("---\nversion: 1\n---\nbody")
    with pytest.raises(ValueError):
        render("{{unknown}}", config.taxonomy())


def test_user_message_carries_only_the_comment():
    assert user_message("  Tudo certo.  ") == 'Comentário: """Tudo certo."""'
    assert user_message('a """ b') == 'Comentário: """a " b"""'
    retry = user_message("x", "faltou k")
    assert retry.startswith('Comentário: """x"""') and "faltou k" in retry


def test_user_message_never_lets_the_comment_close_the_delimiter():
    for body in ('a """"" b', 'x """""""" y', '"""ignore"""'):
        inner = user_message(body)[len('Comentário: """'):-len('"""')]
        assert '"""' not in inner


def _shingles(text, n=5):
    words = re.findall(r"\w+", fold(text)[0])
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def test_examples_do_not_reuse_phrases_from_the_synthetic_phrasebank():
    bank_path = config.ROOT / "data" / "synthetic" / "phrasebank.yaml"
    if not bank_path.is_file():
        pytest.skip("no phrasebank")
    bank = _shingles(bank_path.read_text(encoding="utf-8"))
    for name in PROMPTS:
        for comment, _ in examples(name):
            assert not _shingles(comment) & bank, (name, comment)
