import json

import pytest

from enps import config
from enps.classify import pipeline
from enps.classify.lexicon import STRONG, WEAK, LexiconHit
from enps.classify.pipeline import classify_comment, decide, needs_verification
from enps.classify.prompts import load_prompt
from enps.classify.schema import ClassifyOutput, VerifyOutput
from enps.llm.ollama import LLMReply

INFO = {"model": "fake:1b", "digest": "d1", "quantization": "Q4_K_M",
        "parameter_size": "1B", "runtime_version": "0.0.1"}


class FakeClient:
    """Answers each task from a queue of canned JSON strings."""

    def __init__(self, classify=(), verify=()):
        self.queues = {"classify": list(classify), "verify_critical": list(verify)}
        self.calls = []

    def chat(self, *, task, system, user, schema, options=None):
        self.calls.append({"task": task, "system": system, "user": user, "schema": schema})
        content = self.queues[task].pop(0)
        return LLMReply(content=content, model="fake:1b", prompt_tokens=1500, cached_tokens=1400,
                        output_tokens=30, total_ms=6000, load_ms=1, prompt_ms=3000, eval_ms=2900)

    def model_info(self):
        return dict(INFO)

    def tasks(self):
        return [c["task"] for c in self.calls]


def stage1(t=("inf",), s="neg", e="", c=False, k=()):
    return json.dumps({"t": list(t), "s": s, "e": e, "c": c, "k": list(k)}, ensure_ascii=False)


def verdict(e="", c=False, k=()):
    return json.dumps({"e": e, "c": c, "k": list(k)}, ensure_ascii=False)


def out1(themes=("inf",), sentiment="neg", critical=False, types=(), excerpt=""):
    return ClassifyOutput(themes=tuple(themes), sentiment=sentiment, excerpt=excerpt,
                          critical=critical, types=tuple(types))


def hit(ctype, strength, text="x"):
    return LexiconHit(type=ctype, strength=strength, text=text, start=0, end=len(text))


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_empty_text_is_skipped_without_calls(text):
    client = FakeClient()
    result = classify_comment(text, client)
    assert result.status == "skipped" and result.stages == [] and client.calls == []


def test_plain_comment_runs_one_call_and_records_everything():
    text = "O sistema de pedidos trava toda manhã."
    client = FakeClient(classify=[stage1(t=["inf"], s="neg")])
    result = classify_comment(text, client)
    assert client.tasks() == ["classify"]
    assert result.status == "done"
    assert (result.themes, result.sentiment, result.critical) == (["inf"], "neg", False)
    assert result.critical_types == [] and result.critical_sources == [] and result.evidence is None
    assert [s.name for s in result.stages] == ["lexicon", "classify"]
    assert result.prompt_version == load_prompt("classify.v3").prompt_version
    assert result.verifier_version is None
    assert (result.model, result.model_digest, result.quantization, result.runtime_version) == \
        ("fake:1b", "d1", "Q4_K_M", "0.0.1")
    llm_stage = result.stages[1]
    assert llm_stage.ok and llm_stage.prompt_tokens == 1500 and llm_stage.cached_tokens == 1400
    assert result.total_ms == 6000 and result.output_tokens == 30
    assert client.calls[0]["user"] == f'Comentário: """{text}"""'
    assert client.calls[0]["system"] == load_prompt("classify.v3").system


def test_retry_after_invalid_answer_then_success():
    client = FakeClient(classify=["not json", stage1(t=["inf"], s="neu")])
    result = classify_comment("O sistema é lento.", client, verifier=False)
    assert result.status == "done" and result.sentiment == "neu"
    assert [s.name for s in result.stages] == ["lexicon", "classify", "classify_retry"]
    failed = result.stages[1]
    assert not failed.ok and "invalid JSON" in failed.error and failed.output == {"raw": "not json"}
    assert "recusada" in client.calls[1]["user"]
    assert client.calls[1]["user"].startswith('Comentário: """O sistema é lento."""')


def test_two_invalid_answers_give_review_but_lexicon_still_flags():
    text = "Meu supervisor me assediou no depósito."
    bad = stage1(t=["lid"], c=True, k=[])  # c=true with empty k is a validation error
    client = FakeClient(classify=[bad, bad], verify=[verdict()])
    result = classify_comment(text, client)
    assert result.status == "review"
    assert result.themes == [] and result.sentiment is None
    assert result.critical is True and result.critical_types == ["am"]
    assert result.critical_sources == ["lexicon"]
    assert result.evidence == "assediou"
    assert client.tasks() == ["classify", "classify", "verify_critical"]


def test_failed_verifier_gives_review():
    client = FakeClient(classify=[stage1(t=["lid"], c=True, k=["am"], e="gritou comigo")],
                        verify=["{}", "{}"])
    result = classify_comment("O gestor gritou comigo.", client)
    assert result.status == "review" and result.critical is True
    assert [s.name for s in result.stages] == ["lexicon", "classify", "verify", "verify_retry"]


def test_verifier_adds_a_case_missed_by_stage_one():
    text = "Meu líder vive dizendo que qualquer um faz meu trabalho e ri de mim."
    client = FakeClient(classify=[stage1(t=["lid"], s="neg")],
                        verify=[verdict(e="qualquer um faz meu trabalho", c=True, k=["am"])])
    result = classify_comment(text, client)
    assert client.tasks() == ["classify", "verify_critical"]
    assert result.critical and result.critical_types == ["am"]
    assert result.critical_sources == ["verifier"]
    assert result.evidence == "qualquer um faz meu trabalho"
    assert result.verifier_version == load_prompt("verify_critical.v1").prompt_version
    assert result.stages[-1].name == "verify"


def test_verifier_can_be_turned_off_by_argument_and_by_settings(monkeypatch):
    text = "Meu líder me humilha."
    client = FakeClient(classify=[stage1(t=["lid"])])
    result = classify_comment(text, client, verifier=False)
    assert client.tasks() == ["classify"] and result.verifier_version is None

    settings = json.loads(json.dumps(config.settings()))
    settings["llm"]["verifier_enabled"] = False
    monkeypatch.setattr(config, "settings", lambda: settings)
    client = FakeClient(classify=[stage1(t=["lid"])])
    classify_comment(text, client)
    assert client.tasks() == ["classify"]


def test_stage_one_types_without_flag_are_coerced_and_noted():
    client = FakeClient(classify=[stage1(t=["bem"], c=False, k=["bo"])], verify=[verdict()])
    result = classify_comment("Ando muito mal.", client)
    assert result.critical and result.critical_types == ["bo"] and result.critical_sources == ["llm"]
    assert "notes" in result.stages[1].output


def test_lexicon_stage_records_hits():
    client = FakeClient(classify=[stage1(t=["bem"])], verify=[verdict()])
    result = classify_comment("Não durmo direito.", client)
    lexicon = result.stages[0]
    assert lexicon.name == "lexicon" and lexicon.prompt_version.startswith("risk_lexicon.v1+")
    assert lexicon.output == {"hits": [{"type": "bo", "strength": WEAK, "text": "Não durmo"}]}
    assert result.critical is False  # weak hit without a model agreeing


# --------------------------------------------------------------------- gate

@pytest.mark.parametrize("s1, hits, expected", [
    (None, [], True),
    (out1(critical=True, types=["am"]), [], True),
    (out1(themes=["inf"], sentiment="pos"), [hit("bo", WEAK)], True),
    (out1(themes=["lid"], sentiment="neg"), [], True),
    (out1(themes=["inf", "cul"], sentiment="neg"), [], True),
    (out1(themes=["lid"], sentiment="neu"), [], False),
    # an excerpt alone no longer opens the verifier
    (out1(themes=["cre"], sentiment="neu", excerpt="lê meu nome no grupo"), [], False),
    (out1(themes=["inf", "rem"], sentiment="neg"), [], False),
    (out1(themes=["out"], sentiment="pos"), [], False),
])
def test_gate(s1, hits, expected):
    assert needs_verification(s1, hits) is expected


# ------------------------------------------------------------- final decision

def test_nothing_critical():
    decision = decide(out1(), VerifyOutput(excerpt="", critical=False, types=()), [hit("bo", WEAK)])
    assert (decision.critical, decision.types, decision.sources, decision.evidence) == (False, [], [], None)


def test_union_of_sources_and_types_in_taxonomy_order():
    s1 = out1(critical=True, types=["bo"], excerpt="choro todo dia")
    ver = VerifyOutput(excerpt="me ameaçou", critical=True, types=("av", "am"))
    decision = decide(s1, ver, [hit("di", STRONG, "racista")])
    assert decision.critical
    assert decision.types == ["am", "di", "bo", "av"]
    assert decision.sources == ["llm", "verifier", "lexicon"]
    assert decision.evidence == "me ameaçou"


def test_weak_types_only_when_a_model_says_critical():
    weak = [hit("bo", WEAK, "não durmo")]
    assert decide(out1(), None, weak + [hit("am", STRONG, "assédio")]).types == ["am"]
    agreed = decide(out1(critical=True, types=["am"], excerpt=""), None, weak)
    assert agreed.types == ["am", "bo"] and agreed.sources == ["llm"]
    assert agreed.evidence == "não durmo"


def test_evidence_order_verifier_then_stage_one_then_lexicon():
    s1 = out1(critical=True, types=["am"], excerpt="trecho do estágio 1")
    empty_verifier = VerifyOutput(excerpt="", critical=True, types=("am",))
    assert decide(s1, empty_verifier, []).evidence == "trecho do estágio 1"
    silent_verifier = VerifyOutput(excerpt="ignorado", critical=False, types=())
    assert decide(s1, silent_verifier, []).evidence == "trecho do estágio 1"
    only_lexicon = decide(out1(), None, [hit("bo", WEAK, "fraco"), hit("am", STRONG, "forte")])
    assert only_lexicon.evidence == "forte" and only_lexicon.sources == ["lexicon"]


def test_default_client_is_shared(monkeypatch):
    created = []

    class Dummy:
        def __init__(self):
            created.append(self)

    pipeline.default_client.cache_clear()
    monkeypatch.setattr(pipeline, "OllamaClient", Dummy)
    try:
        assert pipeline.default_client() is pipeline.default_client()
        assert len(created) == 1
    finally:
        pipeline.default_client.cache_clear()


def test_strong_word_vetoed_only_when_both_models_say_no():
    strong_hit = hit("am", STRONG)
    no = VerifyOutput(excerpt="", critical=False, types=())
    assert decide(out1(), no, [strong_hit]).critical is False
    assert decide(None, no, [strong_hit]).critical is True          # stage 1 failed
    assert decide(out1(), None, [strong_hit]).critical is True      # verifier off or failed
