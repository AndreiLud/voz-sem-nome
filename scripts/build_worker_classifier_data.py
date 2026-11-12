"""Write worker/classify/data.js, the data of the Worker classifier.

The Worker port of the classifier (worker/classify/) cannot read YAML or Markdown files, so this
script renders everything it needs with the Python code itself:

    .venv/bin/python scripts/build_worker_classifier_data.py            # write the file
    .venv/bin/python scripts/build_worker_classifier_data.py --check    # exit 1 when it is stale

Contents: the taxonomy codes and labels, the rendered system prompts and their prompt_version
strings (enps.classify.prompts), the JSON schemas (enps.classify.schema), the risk lexicon with
every pattern converted to a JavaScript regular expression, the verifier gate themes
(enps.classify.pipeline) and the model call settings. Prompt names and call settings come from
config/settings.yaml; ENPS_LLM_* overrides of those keys are refused, so the output only depends
on the repository.

Python and JavaScript regular expressions differ in a few places. The converter rewrites what has
a faithful equivalent and refuses the rest:

* Python's \\w, \\d and \\b are Unicode-aware, JavaScript's are ASCII-only, and the two \\s lists
  differ. They become explicit classes: \\p{L}\\p{N}_ for \\w and \\p{Nd} for \\d
  (check_unicode_classes() proves both equal Python's definitions), and the exact list of
  str.isspace() characters for \\s. Negations and \\b are built from these.
* "." excludes only "\\n" in Python, "$" also matches before a final newline, a lone "]" or "}"
  is a literal, and "{,n}" means "{0,n}". Each is rewritten.
* Inline flags, atomic groups, possessive quantifiers, conditionals and backreferences are refused.

Every converted pattern is then checked against Python on shared probe texts by
tests/test_classifier_cases.py and worker/test/classify.test.js.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from enps import config  # noqa: E402
from enps.classify.lexicon import STRONG, WEAK, default_lexicon  # noqa: E402
from enps.classify.pipeline import GATE_THEMES  # noqa: E402
from enps.classify.prompts import Prompt, load_prompt  # noqa: E402
from enps.classify.schema import (  # noqa: E402
    EXCERPT_MAX_CHARS, OUT_THEME, _current_models, classify_schema, verify_schema,
)

OUT = ROOT / "worker" / "classify" / "data.js"
SETTINGS_FILE = ROOT / "config" / "settings.yaml"
LLM_KEYS = ("classify_prompt", "verify_prompt", "temperature", "seed", "num_predict", "verifier_enabled")
MAX_CODE_POINT = 0x110000
WIDTH = 100

HEADER = """\
// Data of the Worker classifier. Do not edit: this file is written by
// scripts/build_worker_classifier_data.py from config/taxonomy.yaml, config/settings.yaml,
// config/risk_lexicon.yaml and prompts/, with the Python classifier code, and
// tests/test_classifier_cases.py fails when it is stale.
"""


class RegexConversionError(ValueError):
    """A Python pattern uses syntax that has no faithful JavaScript equivalent."""


# ------------------------------------------------------------------ character classes

def _escape_code_point(cp: int) -> str:
    if cp < 0x100:
        return f"\\x{cp:02x}"
    if cp < 0x10000:
        return f"\\u{cp:04x}"
    return f"\\u{{{cp:x}}}"


def _ranges(code_points: Iterable[int]) -> list[tuple[int, int]]:
    ranges: list[list[int]] = []
    for cp in sorted(code_points):
        if ranges and ranges[-1][1] == cp - 1:
            ranges[-1][1] = cp
        else:
            ranges.append([cp, cp])
    return [(a, b) for a, b in ranges]


def class_body(code_points: Iterable[int]) -> str:
    """Body of a JavaScript character class (without brackets) holding exactly these code points."""
    parts = []
    for first, last in _ranges(code_points):
        parts.append(_escape_code_point(first) if first == last
                     else f"{_escape_code_point(first)}-{_escape_code_point(last)}")
    return "".join(parts)


WORD_BODY = r"\p{L}\p{N}_"
DIGIT = r"\p{Nd}"
NOT_DIGIT = r"\P{Nd}"
SPACE_BODY = class_body(cp for cp in range(MAX_CODE_POINT) if chr(cp).isspace())
COMBINING_BODY = class_body(cp for cp in range(MAX_CODE_POINT) if unicodedata.combining(chr(cp)))
WORD = f"[{WORD_BODY}]"
NOT_WORD = f"[^{WORD_BODY}]"
SPACE = f"[{SPACE_BODY}]"
NOT_SPACE = f"[^{SPACE_BODY}]"
BOUNDARY = f"(?:(?<={WORD})(?!{WORD})|(?<!{WORD})(?={WORD}))"
NOT_BOUNDARY = f"(?:(?<={WORD})(?={WORD})|(?<!{WORD})(?!{WORD}))"


def check_unicode_classes() -> None:
    """Prove that the JavaScript classes used for \\w, \\d and str.isalnum() match Python's.

    Python defines \\w as str.isalnum() plus "_" and \\d as category Nd. JavaScript reads
    \\p{L} and \\p{N} from its own Unicode tables, so the two agree up to the Unicode version.
    """
    word, digit = re.compile(r"\w"), re.compile(r"\d")
    for cp in range(MAX_CODE_POINT):
        ch = chr(cp)
        category = unicodedata.category(ch)
        letter_or_number = category[0] in "LN"
        if bool(word.match(ch)) != (letter_or_number or ch == "_"):
            raise AssertionError(f"\\w differs from [{WORD_BODY}] at U+{cp:04X}")
        if ch.isalnum() != letter_or_number:
            raise AssertionError(f"str.isalnum() differs from [\\p{{L}}\\p{{N}}] at U+{cp:04X}")
        if bool(digit.match(ch)) != (category == "Nd"):
            raise AssertionError(f"\\d differs from {DIGIT} at U+{cp:04X}")


# ------------------------------------------------------------------ regex conversion

_JS_SYNTAX = set("^$\\.*+?()[]{}|/")
_SIMPLE_ESCAPES = {"n": "\\n", "t": "\\t", "r": "\\r", "f": "\\f", "v": "\\v", "a": "\\x07"}
_OUTSIDE_CLASS = {"w": WORD, "W": NOT_WORD, "d": DIGIT, "D": NOT_DIGIT, "s": SPACE,
                  "S": NOT_SPACE, "b": BOUNDARY, "B": NOT_BOUNDARY, "A": "^", "Z": "$"}
_INSIDE_CLASS = {"w": WORD_BODY, "d": DIGIT, "D": NOT_DIGIT, "s": SPACE_BODY, "b": "\\x08"}
_QUANTIFIER = re.compile(r"\{([0-9]*)(?:(,)([0-9]*))?\}")


class _Converter:
    """Single pass over a Python pattern that re.compile() already accepted."""

    def __init__(self, pattern: str):
        self.p = pattern
        self.i = 0
        self.out: list[str] = []

    def fail(self, what: str) -> RegexConversionError:
        return RegexConversionError(f"{what} at {self.i} in {self.p!r} has no JavaScript equivalent")

    def peek(self, offset: int = 0) -> str:
        j = self.i + offset
        return self.p[j] if j < len(self.p) else ""

    def run(self) -> str:
        while self.i < len(self.p):
            ch = self.p[self.i]
            if ch == "\\":
                self.out.append(self.escape(in_class=False))
            elif ch == "[":
                self.out.append(self.char_class())
            elif ch == "(":
                self.out.append(self.group())
            elif ch in "*+?":
                self.i += 1
                self.out.append(ch + self.quantifier_suffix())
            elif ch == "{":
                self.out.append(self.brace())
            elif ch == ".":
                self.i += 1
                self.out.append("[^\\n]")
            elif ch == "$":
                self.i += 1
                self.out.append("(?=\\n?$)")  # Python: end, or before a newline at the end
            elif ch in "]}":
                self.i += 1
                self.out.append("\\" + ch)
            else:  # ) | ^ and literal characters mean the same in both
                self.i += 1
                self.out.append(ch)
        return "".join(self.out)

    def quantifier_suffix(self) -> str:
        if self.peek() == "?":
            self.i += 1
            return "?"
        if self.peek() == "+":
            raise self.fail("possessive quantifier")
        return ""

    def brace(self) -> str:
        match = _QUANTIFIER.match(self.p, self.i)
        if not match or match.group(0) == "{}":
            self.i += 1
            return "\\{"  # a literal brace in Python, a syntax error in JavaScript
        self.i = match.end()
        low, comma, high = match.group(1) or "0", match.group(2), match.group(3)
        body = f"{{{low},{high}}}" if comma else f"{{{low}}}"
        return body + self.quantifier_suffix()

    def group(self) -> str:
        if self.peek(1) != "?":
            self.i += 1
            return "("
        kind = self.peek(2)
        if kind in ":=!":
            self.i += 3
            return f"(?{kind}"
        if kind == "<" and self.peek(3) in "=!":
            self.i += 4
            return f"(?<{self.p[self.i - 1]}"
        if kind == "P" and self.peek(3) == "<":
            end = self.p.index(">", self.i)
            name = self.p[self.i + 4:end]
            self.i = end + 1
            return f"(?<{name}>"
        if kind == "#":
            self.i = self.p.index(")", self.i) + 1
            return ""
        raise self.fail(f"group syntax (?{kind}")

    def escape(self, *, in_class: bool) -> str:
        ch = self.peek(1)
        self.i += 2
        table = _INSIDE_CLASS if in_class else _OUTSIDE_CLASS
        if ch in table:
            return table[ch]
        if ch in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[ch]
        hex_digits = {"x": 2, "u": 4, "U": 8}.get(ch)
        if hex_digits:
            cp = int(self.p[self.i:self.i + hex_digits], 16)
            self.i += hex_digits
            return _escape_code_point(cp)
        if ch == "N":
            end = self.p.index("}", self.i)
            cp = ord(unicodedata.lookup(self.p[self.i + 1:end]))
            self.i = end + 1
            return _escape_code_point(cp)
        if ch.isascii() and ch.isalnum():
            raise self.fail(f"escape \\{ch}")
        return self.literal(ch, in_class=in_class)

    @staticmethod
    def literal(ch: str, *, in_class: bool) -> str:
        if ch in _JS_SYNTAX or (in_class and ch == "-"):
            return "\\" + ch
        if not ch.isprintable():
            return _escape_code_point(ord(ch))
        return ch

    def char_class(self) -> str:
        self.i += 1
        parts = ["["]
        if self.peek() == "^":
            parts.append("^")
            self.i += 1
        first = True
        while True:
            ch = self.peek()
            if ch == "]" and not first:
                self.i += 1
                break
            first = False
            if ch == "\\":
                if self.peek(1) in ("W", "S"):
                    raise self.fail(f"\\{self.peek(1)} inside a character class")
                parts.append(self.escape(in_class=True))
            elif ch == "-":
                self.i += 1
                parts.append("-")
            else:
                self.i += 1
                parts.append(self.literal(ch, in_class=True))
        parts.append("]")
        return "".join(parts)


def to_js_regex(pattern: str, flags: int = 0) -> tuple[str, str]:
    """(source, flags) of the JavaScript regex that matches like the Python one."""
    compiled = re.compile(pattern, flags)
    extra = compiled.flags & ~re.UNICODE
    if extra:
        raise RegexConversionError(f"flags {re.RegexFlag(extra)!r} of {pattern!r} are not supported")
    return _Converter(pattern).run(), "u"


# ------------------------------------------------------------------ data

def _settings_llm() -> dict[str, Any]:
    """The llm section of settings.yaml, refusing environment overrides of the keys used here."""
    with open(SETTINGS_FILE, encoding="utf-8") as fh:
        from_file = yaml.safe_load(fh)["llm"]
    live = config.settings()["llm"]
    changed = [k for k in LLM_KEYS if live[k] != from_file[k]]
    if changed:
        raise SystemExit(f"ENPS_LLM_* environment overrides are set for {changed}: unset them first")
    return {k: from_file[k] for k in LLM_KEYS}


def _taxonomy() -> dict[str, Any]:
    taxonomy = config.taxonomy()
    return {group: [{"code": item["code"], "label": item["label"]} for item in taxonomy[group]]
            for group in ("themes", "sentiments", "critical_types")}


def _prompt(prompt: Prompt) -> dict[str, Any]:
    return {"name": prompt.name, "task": prompt.task, "version": prompt.version,
            "prompt_version": prompt.prompt_version, "system": prompt.system}


def _lexicon() -> dict[str, Any]:
    lexicon = default_lexicon()
    words = sorted(lexicon.negation_words)
    if not all(isinstance(w, str) for w in words):
        raise ValueError("negation words must be strings")
    rules = []
    for rule in lexicon.rules:
        if rule.strength not in (STRONG, WEAK):
            raise ValueError(f"unknown strength {rule.strength!r}")
        source, flags = to_js_regex(rule.regex.pattern, rule.regex.flags & ~re.UNICODE)
        rules.append({"type": rule.type, "strength": rule.strength, "source": source, "flags": flags})
    return {"version": lexicon.version,
            "negation": {"window": lexicon.negation_window, "words": words},
            "rules": rules}


def _defaults() -> dict[str, dict[str, Any]]:
    """Value of each optional schema field when the answer leaves it out (pydantic defaults)."""
    out = {}
    for key, model in zip(("classify", "verify"), _current_models()):
        out[key] = {name: field.get_default(call_default_factory=True)
                    for name, field in model.model_fields.items() if not field.is_required()}
    return out


def build() -> dict[str, Any]:
    """Everything data.js exports, as plain JSON values."""
    llm = _settings_llm()
    codes = config.theme_codes()
    return {
        "TAXONOMY": _taxonomy(),
        "LLM": {"temperature": llm["temperature"], "seed": llm["seed"],
                "max_tokens": llm["num_predict"], "verifier_enabled": llm["verifier_enabled"]},
        "PROMPTS": {"classify": _prompt(load_prompt(llm["classify_prompt"])),
                    "verify": _prompt(load_prompt(llm["verify_prompt"]))},
        "SCHEMAS": {"classify": classify_schema(), "verify": verify_schema()},
        "SCHEMA_DEFAULTS": _defaults(),
        "EXCERPT_MAX_CHARS": EXCERPT_MAX_CHARS,
        "OUT_THEME": OUT_THEME,
        "GATE_THEMES": sorted(GATE_THEMES, key=codes.index),
        "LEXICON": _lexicon(),
        "PY_SPACE": SPACE_BODY,
        "PY_COMBINING": COMBINING_BODY,
    }


# ------------------------------------------------------------------ rendering

COMMENTS = {
    "TAXONOMY": "Codes and labels of config/taxonomy.yaml, in taxonomy order.",
    "LLM": "Model call settings from the llm section of config/settings.yaml (max_tokens is num_predict).",
    "PROMPTS": "System prompts rendered by enps.classify.prompts, with the prompt_version they record.",
    "SCHEMAS": "JSON schemas of enps.classify.schema: classify_schema() and verify_schema().",
    "SCHEMA_DEFAULTS": ("Fields the validation fills in when an answer leaves them out (the pydantic\n"
                        "// defaults); every other schema field is required."),
    "EXCERPT_MAX_CHARS": "Longest excerpt kept, in code points (enps.classify.schema).",
    "OUT_THEME": "The theme that is only valid alone.",
    "GATE_THEMES": "A negative comment on one of these themes goes to the verifier (enps.classify.pipeline).",
    "LEXICON": ("config/risk_lexicon.yaml with macros expanded and each whole-word pattern converted to\n"
                "// a JavaScript regex. Rules run on the folded text, in this order."),
    "PY_SPACE": "Character class body of Python's whitespace (str.isspace, \\s).",
    "PY_COMBINING": ("Character class body of the code points with a nonzero canonical combining class\n"
                     "// (unicodedata.combining), which the lexicon drops when it folds accents."),
}


def _js(value: Any, indent: int = 0) -> str:
    """JSON (valid JavaScript) that keeps a container on one line when it fits in WIDTH."""
    flat = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, (dict, list)) or len(flat) + indent <= WIDTH or not value:
        return flat
    pad = " " * (indent + 2)
    if isinstance(value, dict):
        items = [f"{pad}{json.dumps(k, ensure_ascii=False)}: {_js(v, indent + 2)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    items = [f"{pad}{_js(v, indent + 2)}" for v in value]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


def _render_prompts(prompts: dict[str, Any]) -> str:
    blocks = []
    for key, prompt in prompts.items():
        fields = [f"    {name}: {_js(prompt[name])}," for name in ("name", "task", "version", "prompt_version")]
        lines = ",\n".join(f"      {_js(line)}" for line in prompt["system"].split("\n"))
        fields.append(f'    system: [\n{lines},\n    ].join("\\n"),')
        blocks.append(f"  {key}: {{\n" + "\n".join(fields) + "\n  },")
    return "{\n" + "\n".join(blocks) + "\n}"


def _render_lexicon(lexicon: dict[str, Any]) -> str:
    rules = ",\n".join(f"    {json.dumps(rule, ensure_ascii=False)}" for rule in lexicon["rules"])
    return ("{\n"
            f"  version: {_js(lexicon['version'])},\n"
            f"  negation: {_js(lexicon['negation'])},\n"
            f"  rules: [\n{rules},\n  ],\n"
            "}")


def _render_class_body(body: str) -> str:
    """A long class body split at range boundaries into lines joined at load."""
    pieces = re.findall(r"(?:\\u\{[0-9a-f]+\}|\\u[0-9a-f]{4}|\\x[0-9a-f]{2})"
                        r"(?:-(?:\\u\{[0-9a-f]+\}|\\u[0-9a-f]{4}|\\x[0-9a-f]{2}))?", body)
    if "".join(pieces) != body:
        raise ValueError("unexpected class body")
    lines, line = [], ""
    for piece in pieces:
        if line and len(line) + len(piece) > WIDTH - 10:
            lines.append(line)
            line = ""
        line += piece
    lines.append(line)
    if len(lines) == 1:
        return _js(body)
    return "[\n" + ",\n".join(f"  {_js(x)}" for x in lines) + ',\n].join("")'


def render(data: dict[str, Any] | None = None) -> str:
    """Full text of data.js."""
    data = data or build()
    parts = [HEADER]
    for name, value in data.items():
        if name == "PROMPTS":
            body = _render_prompts(value)
        elif name == "LEXICON":
            body = _render_lexicon(value)
        elif name.startswith("PY_"):
            body = _render_class_body(value)
        else:
            body = _js(value)
        parts.append(f"// {COMMENTS[name]}\nexport const {name} = {body};\n")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report whether data.js is stale")
    args = parser.parse_args(argv)
    check_unicode_classes()
    content = render()
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else None
    if args.check:
        stale = current != content
        print(f"{OUT.relative_to(ROOT)} is {'stale' if stale else 'up to date'}")
        return 1 if stale else 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(content.encode('utf-8'))} bytes, "
          f"{len(build()['LEXICON']['rules'])} lexicon rules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
