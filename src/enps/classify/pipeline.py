"""Two-stage classification of one anonymized comment.

1. Lexicon: regex hits for explicit (strong) and indirect (weak) risk signals.
2. Stage 1 ("classify"): themes, sentiment and a first critical flag from the local model.
3. Verifier ("verify_critical"): a second reading focused on critical cases, run only when the
   gate holds (stage 1 said critical or copied an excerpt, any lexicon hit, or a negative comment
   on a sensitive theme).

Final decision, recall first: critical = stage 1 OR verifier OR a strong lexicon hit.
A model answer that breaks the schema is retried once with the error message; a second failure
sets the status to "review", and the lexicon still decides whether the case is critical.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Sequence, TypeVar

from enps import config
from enps.classify.lexicon import STRONG, LexiconHit, default_lexicon
from enps.classify.prompts import Prompt, load_prompt, user_message
from enps.classify.schema import (
    ClassifyOutput, OutputError, VerifyOutput, classify_schema, validate_classify,
    validate_verify, verify_schema,
)
from enps.classify.types import ClassificationResult, StageRecord
from enps.llm.ollama import LLMClient, LLMReply, OllamaClient

GATE_THEMES = frozenset({"lid", "eqp", "bem", "car", "cul"})
SOURCE_LLM, SOURCE_VERIFIER, SOURCE_LEXICON = "llm", "verifier", "lexicon"

Output = TypeVar("Output", ClassifyOutput, VerifyOutput)


@lru_cache(maxsize=1)
def default_client() -> OllamaClient:
    return OllamaClient()


def needs_verification(stage1: ClassifyOutput | None, hits: Sequence[LexiconHit]) -> bool:
    """Gate for the verifier. A failed stage 1 (None) always opens it.

    An excerpt alone does not open it: some models copy an excerpt for almost every comment, which
    sent 82% of the dev_quick comments to the verifier without finding a single extra case.
    """
    if stage1 is None or stage1.critical or hits:
        return True
    return stage1.sentiment == "neg" and bool(GATE_THEMES.intersection(stage1.themes))


@dataclass(frozen=True)
class Decision:
    critical: bool
    types: list[str]
    sources: list[str]
    evidence: str | None


def decide(stage1: ClassifyOutput | None, verdict: VerifyOutput | None,
           hits: Sequence[LexiconHit]) -> Decision:
    """Union of the sources that said critical. Weak hits add their type only when a model agrees."""
    said = {name: out for name, out in ((SOURCE_LLM, stage1), (SOURCE_VERIFIER, verdict))
            if out is not None and out.critical}
    # A strong word counts on its own unless both models read the comment and said it is not a case
    # ("o treinamento sobre assédio foi ótimo"). A failed stage 1 never vetoes the lexicon.
    rejected = (stage1 is not None and not stage1.critical
                and verdict is not None and not verdict.critical)
    strong = [h for h in hits if h.strength == STRONG] if not rejected else []
    sources = list(said) + ([SOURCE_LEXICON] if strong else [])
    if not sources:
        return Decision(False, [], [], None)

    found = [t for out in said.values() for t in out.types]
    found += [h.type for h in (hits if said else strong)]
    order = {code: i for i, code in enumerate(config.critical_codes())}
    types = sorted(set(found), key=lambda code: order.get(code, len(order)))

    excerpts = [said[name].excerpt for name in (SOURCE_VERIFIER, SOURCE_LLM) if name in said]
    excerpts += [h.text for h in sorted(hits, key=lambda h: h.strength != STRONG)]
    return Decision(True, types, sources, next((e for e in excerpts if e), None))


def _record(name: str, prompt: Prompt, reply: LLMReply, *, ok: bool, error: str | None,
            output: dict | None) -> StageRecord:
    return StageRecord(
        name=name, prompt_version=prompt.prompt_version, ok=ok, error=error, output=output,
        prompt_tokens=reply.prompt_tokens, cached_tokens=reply.cached_tokens,
        output_tokens=reply.output_tokens, total_ms=reply.total_ms, load_ms=reply.load_ms,
        prompt_ms=reply.prompt_ms, eval_ms=reply.eval_ms, from_cache=reply.from_cache,
    )


def run_stage(client: LLMClient, name: str, prompt: Prompt, text: str, schema: dict,
              validate: Callable[[str, str], Output]) -> tuple[Output | None, list[StageRecord]]:
    """Call the model, validate, and retry once with the error message if needed."""
    records: list[StageRecord] = []
    hint: str | None = None
    for attempt in (name, f"{name}_retry"):
        reply = client.chat(task=prompt.task, system=prompt.system,
                            user=user_message(text, hint), schema=schema)
        try:
            output = validate(reply.content, text)
        except OutputError as exc:
            records.append(_record(attempt, prompt, reply, ok=False, error=str(exc),
                                   output={"raw": reply.content}))
            hint = exc.hint
            continue
        records.append(_record(attempt, prompt, reply, ok=True, error=None, output=output.as_dict()))
        return output, records
    return None, records


def _lexicon_record(hits: Sequence[LexiconHit], version: str) -> StageRecord:
    return StageRecord(name="lexicon", prompt_version=version,
                       output={"hits": [h.as_dict() for h in hits]})


def classify_comment(text_anon: str, client: LLMClient | None = None, *,
                     verifier: bool | None = None) -> ClassificationResult:
    """Classify one anonymized comment. Empty text is skipped without calling the model."""
    if not text_anon or not text_anon.strip():
        return ClassificationResult(status="skipped")
    llm = config.settings()["llm"]
    client = client or default_client()
    use_verifier = llm["verifier_enabled"] if verifier is None else verifier

    lexicon = default_lexicon()
    hits = lexicon.match(text_anon)
    stages = [_lexicon_record(hits, lexicon.version)]

    classify_prompt = load_prompt(llm["classify_prompt"])
    stage1, records = run_stage(client, "classify", classify_prompt, text_anon, classify_schema(),
                                validate_classify)
    stages += records

    verdict: VerifyOutput | None = None
    verify_prompt: Prompt | None = None
    verifier_failed = False
    if use_verifier and needs_verification(stage1, hits):
        verify_prompt = load_prompt(llm["verify_prompt"])
        verdict, records = run_stage(client, "verify", verify_prompt, text_anon, verify_schema(),
                                     validate_verify)
        stages += records
        verifier_failed = verdict is None

    decision = decide(stage1, verdict, hits)
    info = client.model_info()
    return ClassificationResult(
        status="review" if stage1 is None or verifier_failed else "done",
        themes=list(stage1.themes) if stage1 else [],
        sentiment=stage1.sentiment if stage1 else None,
        critical=decision.critical,
        critical_types=decision.types,
        critical_sources=decision.sources,
        evidence=decision.evidence,
        stages=stages,
        model=info.get("model"),
        model_digest=info.get("digest"),
        quantization=info.get("quantization"),
        runtime_version=info.get("runtime_version"),
        prompt_version=classify_prompt.prompt_version,
        verifier_version=verify_prompt.prompt_version if verify_prompt else None,
    )
