"""Result types shared by the classifier, the queue worker, the API and the evaluation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageRecord:
    """One call to the model (or one rule pass) inside the classification of a comment."""
    name: str                      # "classify", "classify_retry", "lexicon", "verify", "verify_retry"
    prompt_version: str | None = None
    ok: bool = True
    error: str | None = None       # validation error that triggered a retry, if any
    output: dict[str, Any] | None = None
    prompt_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    total_ms: int = 0
    load_ms: int = 0
    prompt_ms: int = 0
    eval_ms: int = 0
    from_cache: bool = False


@dataclass
class ClassificationResult:
    status: str                                    # "done", "review" or "skipped"
    themes: list[str] = field(default_factory=list)
    sentiment: str | None = None
    critical: bool = False
    critical_types: list[str] = field(default_factory=list)
    critical_sources: list[str] = field(default_factory=list)  # "llm", "lexicon", "verifier"
    evidence: str | None = None
    stages: list[StageRecord] = field(default_factory=list)
    model: str | None = None
    model_digest: str | None = None
    quantization: str | None = None
    runtime_version: str | None = None
    prompt_version: str | None = None
    verifier_version: str | None = None

    @property
    def total_ms(self) -> int:
        return sum(s.total_ms for s in self.stages)

    @property
    def output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.stages)

    @property
    def prompt_tokens(self) -> int:
        return sum(s.prompt_tokens for s in self.stages)
