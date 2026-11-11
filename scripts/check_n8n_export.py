"""Check exported n8n workflow JSON files for credentials, secrets and personal data.

    python scripts/check_n8n_export.py n8n/workflows/*.json
    python scripts/check_n8n_export.py exported-from-n8n.json   # a file or a list exported by n8n

Exit code 0 when every file is clean, 1 otherwise, with one line per problem.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

# Credential references allowed in the repository: name plus a placeholder id, never the secret itself.
ALLOWED_CREDENTIALS = {("httpHeaderAuth", "internalApiCredential", "Internal API")}
FORBIDDEN_KEYS = {"shared", "instanceId", "homeProject", "ownedBy", "staticData"}
SECRET_PATTERNS = {
    "telegram bot token": re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "google api key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "bearer token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "long random secret": re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
}
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
SENSITIVE_HEADERS = re.compile(r"(?i)^(authorization|x-internal-token|x-origin-secret|cookie|x-api-key)$")


def _walk(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _walk(child, f"{path}[{i}]")


def check_workflow(wf: dict[str, Any], label: str) -> list[str]:
    problems: list[str] = []
    if wf.get("pinData"):
        problems.append(f"{label}: pinData holds sample data from a real execution")
    for node in wf.get("nodes", []):
        for ctype, ref in (node.get("credentials") or {}).items():
            key = (ctype, ref.get("id"), ref.get("name"))
            if key not in ALLOWED_CREDENTIALS:
                problems.append(f"{label}: node {node.get('name')!r} references credential {key}")
            extra = set(ref) - {"id", "name"}
            if extra:
                problems.append(f"{label}: credential reference in {node.get('name')!r} has fields {sorted(extra)}")
        headers = (node.get("parameters") or {}).get("headerParameters", {}).get("parameters", [])
        for header in headers:
            if SENSITIVE_HEADERS.match(str(header.get("name", ""))) and header.get("value"):
                problems.append(f"{label}: node {node.get('name')!r} hardcodes header {header['name']!r}")
    for path, value in _walk(wf):
        last = path.rsplit(".", 1)[-1]
        if last in FORBIDDEN_KEYS and value:
            problems.append(f"{label}: field {path} should not be exported (owner or instance data)")
        if isinstance(value, str):
            for kind, pattern in SECRET_PATTERNS.items():
                if kind == "long random secret" and (path.endswith(".id") or path.endswith("webhookId")
                                                     or "jsCode" in path):
                    continue
                if pattern.search(value):
                    problems.append(f"{label}: {kind} in {path}")
            for match in EMAIL.finditer(value):
                if not match.group(0).endswith((".example", "example.com", "example.org")):
                    problems.append(f"{label}: email address {match.group(0)!r} in {path}")
    return problems


def check_file(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path}: cannot read JSON ({exc})"]
    workflows = data if isinstance(data, list) else [data]
    problems: list[str] = []
    for i, wf in enumerate(workflows):
        label = f"{path.name}" + (f"[{i}]" if isinstance(data, list) else "")
        problems += check_workflow(wf, label)
    return problems


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv] or sorted(Path("n8n/workflows").glob("*.json"))
    problems = [p for path in paths for p in check_file(path)]
    for problem in problems:
        print(problem)
    print(f"checked {len(paths)} file(s): {'clean' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
