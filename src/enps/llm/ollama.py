"""Client for a local Ollama server, with a disk cache keyed on the full request.

In the caching modes every reply is stored under <cache_dir>/<task>/<sha256>.json together with the
request that produced it, so an evaluation can be replayed later without the model (mode "offline").

Modes (settings.llm.mode):
    live         call the model, then write the cache
    cache_first  read the cache, call the model on a miss
    offline      read the cache, raise CacheMissError on a miss
    no_cache     call the model and keep nothing on disk: the queue worker of a deployment, where a cache
                 would keep anonymized comments past the retention period
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Protocol

import httpx

from enps import config

MODES = ("live", "cache_first", "offline", "no_cache")
WRITES_CACHE = ("live", "cache_first")
READS_CACHE = ("cache_first", "offline")
OPTION_KEYS = ("temperature", "seed", "num_ctx", "num_predict", "num_batch")
MODEL_INFO_DIR = "_model_info"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


class LLMError(RuntimeError):
    """The model server could not produce a usable reply."""


class CacheMissError(LLMError):
    """Offline mode was asked for a request that is not in the cache."""


@dataclass
class LLMReply:
    content: str
    model: str
    prompt_tokens: int = 0      # prompt tokens, including the ones served from the prefix cache
    cached_tokens: int = 0      # prompt tokens reused from the runtime prefix cache
    output_tokens: int = 0
    total_ms: int = 0
    load_ms: int = 0
    prompt_ms: int = 0
    eval_ms: int = 0
    from_cache: bool = False    # True when read from the disk cache instead of the model


class LLMClient(Protocol):
    def chat(self, *, task: str, system: str, user: str, schema: dict[str, Any],
             options: dict[str, Any] | None = None) -> LLMReply: ...

    def model_info(self) -> dict[str, Any]: ...


def _ns_to_ms(value: Any) -> int:
    return int(round((value or 0) / 1_000_000))


def reply_from_response(data: dict[str, Any]) -> LLMReply:
    """Build an LLMReply from a non-streaming /api/chat response body."""
    message = data.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise LLMError("Ollama reply has no message content")
    return LLMReply(
        content=content,
        model=str(data.get("model", "")),
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        cached_tokens=int(data.get("prompt_eval_cached_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
        total_ms=_ns_to_ms(data.get("total_duration")),
        load_ms=_ns_to_ms(data.get("load_duration")),
        prompt_ms=_ns_to_ms(data.get("prompt_eval_duration")),
        eval_ms=_ns_to_ms(data.get("eval_duration")),
    )


def cache_key(request: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of the request fields."""
    blob = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ResponseCache:
    """Replies on disk, one JSON file per request."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, task: str, key: str) -> Path:
        return self.root / task / f"{key}.json"

    def get(self, task: str, key: str) -> LLMReply | None:
        path = self.path(task, key)
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)["reply"]
        known = {f.name for f in fields(LLMReply)}
        reply = LLMReply(**{k: v for k, v in stored.items() if k in known})
        reply.from_cache = True
        return reply

    def put(self, task: str, key: str, request: dict[str, Any], reply: LLMReply) -> None:
        stored = asdict(reply)
        stored.pop("from_cache")
        _write_json_atomic(self.path(task, key), {"key": key, "request": request, "reply": stored})

    def _info_path(self, model: str) -> Path:
        return self.root / MODEL_INFO_DIR / f"{_SAFE_NAME.sub('_', model)}.json"

    def get_model_info(self, model: str) -> dict[str, Any] | None:
        path = self._info_path(model)
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def put_model_info(self, info: dict[str, Any]) -> None:
        _write_json_atomic(self._info_path(info["model"]), info)


class OllamaClient:
    """Calls POST /api/chat with a JSON schema as `format`; one request at a time."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        mode: str | None = None,
        cache_dir: str | Path | None = None,
        timeout_s: float | None = None,
        keep_alive: int | str | None = None,
        options: dict[str, Any] | None = None,
        http_client: httpx.Client | None = None,
    ):
        llm = config.settings()["llm"]
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL") or llm["base_url"]).rstrip("/")
        self.model = model or llm["model"]
        self.mode = mode or llm["mode"]
        if self.mode not in MODES:
            raise ValueError(f"unknown llm mode {self.mode!r}, expected one of {MODES}")
        self.cache = ResponseCache(config.resolve(cache_dir or llm["cache_dir"]))
        self.keep_alive = llm["keep_alive"] if keep_alive is None else keep_alive
        self.options = {k: llm[k] for k in OPTION_KEYS if k in llm}
        self.options.update(options or {})
        self._http = http_client or httpx.Client(timeout=timeout_s or llm["timeout_s"])
        self._info: dict[str, Any] | None = None

    # ----------------------------------------------------------------- chat

    def chat(self, *, task: str, system: str, user: str, schema: dict[str, Any],
             options: dict[str, Any] | None = None) -> LLMReply:
        if not re.fullmatch(r"[a-z0-9_][a-z0-9_.-]*", task):  # a folder name, never "." or ".."
            raise ValueError(f"invalid task name {task!r}")
        opts = {**self.options, **(options or {})}
        request = {"model": self.model, "task": task, "system": system, "user": user,
                   "schema": schema, "options": opts}
        key = cache_key(request)
        if self.mode in READS_CACHE:
            cached = self.cache.get(task, key)
            if cached is not None:
                return cached
            if self.mode == "offline":
                raise CacheMissError(f"no cached reply for task {task!r} (key {key[:12]})")
        reply = self._call(system=system, user=user, schema=schema, options=opts)
        if self.mode in WRITES_CACHE:
            self.cache.put(task, key, request, reply)
        return reply

    def _call(self, *, system: str, user: str, schema: dict[str, Any],
              options: dict[str, Any]) -> LLMReply:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "think": False,
            "format": schema,
            "options": options,
            "keep_alive": self.keep_alive,
        }
        return reply_from_response(self._request("POST", "/api/chat", json=body))

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._http.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(f"Ollama {path} returned {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(f"Ollama {path} returned invalid JSON") from exc

    # ----------------------------------------------------------- model info

    def model_info(self) -> dict[str, Any]:
        """Model name, digest, quantization, parameter size and Ollama version (cached)."""
        if self._info is None:
            self._info = self._load_model_info()
        return dict(self._info)

    def _load_model_info(self) -> dict[str, Any]:
        if self.mode == "offline":
            return self.cache.get_model_info(self.model) or {
                "model": self.model, "digest": None, "quantization": None,
                "parameter_size": None, "runtime_version": None}
        if self.mode == "no_cache":
            return self._fetch_model_info()
        try:
            info = self._fetch_model_info()
        except LLMError:
            stored = self.cache.get_model_info(self.model)
            if stored is None:
                raise
            return stored
        self.cache.put_model_info(info)
        return info

    def _fetch_model_info(self) -> dict[str, Any]:
        tags = self._request("GET", "/api/tags").get("models", [])
        entry = next((m for m in tags if self.model in (m.get("name"), m.get("model"))), {})
        details = {**(entry.get("details") or {}),
                   **(self._request("POST", "/api/show", json={"model": self.model}).get("details") or {})}
        version = self._request("GET", "/api/version").get("version")
        return {
            "model": self.model,
            "digest": entry.get("digest"),
            "quantization": details.get("quantization_level"),
            "parameter_size": details.get("parameter_size"),
            "runtime_version": version,
        }
