import json

import httpx
import pytest

from enps.llm.ollama import (
    CacheMissError, LLMError, OllamaClient, cache_key, reply_from_response,
)

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

CHAT_RESPONSE = {
    "model": "qwen3.5:2b-q4_K_M",
    "message": {"role": "assistant", "content": '{"ok": true}'},
    "done": True,
    "total_duration": 2_463_355_850,
    "load_duration": 817_767,
    "prompt_eval_count": 27,
    "prompt_eval_cached_count": 20,
    "prompt_eval_duration": 1_471_312_000,
    "eval_count": 10,
    "eval_duration": 973_379_000,
}


class FakeServer:
    def __init__(self, fail_info: bool = False):
        self.requests: list[httpx.Request] = []
        self.fail_info = fail_info

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/api/chat":
            return httpx.Response(200, json=CHAT_RESPONSE)
        if self.fail_info:
            return httpx.Response(503, text="down")
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "other:1b", "digest": "zzz"},
                {"name": "qwen3.5:2b-q4_K_M", "digest": "abc123",
                 "details": {"parameter_size": "1.9B", "quantization_level": "Q4_K_M"}}]})
        if path == "/api/show":
            return httpx.Response(200, json={"details": {"quantization_level": "Q4_K_M",
                                                         "parameter_size": "1.9B"}})
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        return httpx.Response(404)

    def calls(self, path: str) -> int:
        return sum(r.url.path == path for r in self.requests)


def make_client(tmp_path, server, mode="live", **kwargs):
    http = httpx.Client(transport=httpx.MockTransport(server.handler))
    return OllamaClient(base_url="http://test:11434", model="qwen3.5:2b-q4_K_M", mode=mode,
                        cache_dir=tmp_path / "cache", http_client=http, **kwargs)


def chat(client, user="Comentário: \"\"\"oi\"\"\"", system="sys", options=None):
    return client.chat(task="classify", system=system, user=user, schema=SCHEMA, options=options)


def test_reply_parsing_converts_durations_and_counts():
    reply = reply_from_response(CHAT_RESPONSE)
    assert reply.content == '{"ok": true}'
    assert (reply.prompt_tokens, reply.cached_tokens, reply.output_tokens) == (27, 20, 10)
    assert (reply.total_ms, reply.load_ms, reply.prompt_ms, reply.eval_ms) == (2463, 1, 1471, 973)
    assert reply.from_cache is False


def test_reply_without_content_is_an_error():
    with pytest.raises(LLMError):
        reply_from_response({"model": "x", "message": {}})


def test_request_body_uses_settings_options(tmp_path):
    server = FakeServer()
    client = make_client(tmp_path, server)
    chat(client, options={"num_predict": 64})
    body = json.loads(server.requests[0].content)
    assert str(server.requests[0].url) == "http://test:11434/api/chat"
    assert body["stream"] is False and body["think"] is False
    assert body["keep_alive"] == -1
    assert body["format"] == SCHEMA
    assert body["messages"] == [{"role": "system", "content": "sys"},
                                {"role": "user", "content": 'Comentário: """oi"""'}]
    assert body["options"] == {"temperature": 0, "seed": 42, "num_ctx": 4096,
                               "num_predict": 64, "num_batch": 64}


def test_live_mode_calls_and_writes_cache(tmp_path):
    server = FakeServer()
    client = make_client(tmp_path, server)
    chat(client)
    chat(client)
    assert server.calls("/api/chat") == 2
    files = list((tmp_path / "cache" / "classify").glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["request"]["system"] == "sys"
    assert stored["request"]["model"] == "qwen3.5:2b-q4_K_M"
    assert stored["reply"]["prompt_tokens"] == 27
    assert "from_cache" not in stored["reply"]
    assert files[0].stem == stored["key"] == cache_key(stored["request"])


def test_cache_first_reads_before_calling(tmp_path):
    server = FakeServer()
    client = make_client(tmp_path, server, mode="cache_first")
    first = chat(client)
    second = chat(client)
    assert server.calls("/api/chat") == 1
    assert first.from_cache is False and second.from_cache is True
    assert second.content == first.content and second.prompt_tokens == 27


def test_cache_key_depends_on_every_request_field(tmp_path):
    server = FakeServer()
    client = make_client(tmp_path, server, mode="cache_first")
    chat(client)
    chat(client, system="other system")
    chat(client, user="other user")
    chat(client, options={"num_predict": 10})
    assert server.calls("/api/chat") == 4


def test_offline_mode_reads_cache_and_raises_on_miss(tmp_path):
    server = FakeServer()
    chat(make_client(tmp_path, server))
    offline = make_client(tmp_path, server, mode="offline")
    assert chat(offline).from_cache is True
    with pytest.raises(CacheMissError):
        chat(offline, user="never seen")
    assert server.calls("/api/chat") == 1


def test_invalid_mode_and_task_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        make_client(tmp_path, FakeServer(), mode="sometimes")
    with pytest.raises(ValueError):
        make_client(tmp_path, FakeServer()).chat(task="../x", system="", user="", schema=SCHEMA)


def test_http_error_becomes_llm_error(tmp_path):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    client = OllamaClient(base_url="http://test", mode="live", cache_dir=tmp_path, http_client=http)
    with pytest.raises(LLMError, match="500"):
        chat(client)


def test_model_info_is_fetched_once_and_saved(tmp_path):
    server = FakeServer()
    client = make_client(tmp_path, server)
    info = client.model_info()
    assert info == {"model": "qwen3.5:2b-q4_K_M", "digest": "abc123", "quantization": "Q4_K_M",
                    "parameter_size": "1.9B", "runtime_version": "0.40.16"}
    client.model_info()
    assert server.calls("/api/tags") == 1 and server.calls("/api/version") == 1

    offline = make_client(tmp_path, FakeServer(fail_info=True), mode="offline")
    assert offline.model_info() == info


def test_model_info_falls_back_to_saved_copy_when_server_is_down(tmp_path):
    make_client(tmp_path, FakeServer()).model_info()
    down = make_client(tmp_path, FakeServer(fail_info=True), mode="cache_first")
    assert down.model_info()["digest"] == "abc123"
    with pytest.raises(LLMError):
        make_client(tmp_path / "empty", FakeServer(fail_info=True)).model_info()


def test_offline_model_info_without_saved_copy_has_only_the_name(tmp_path):
    info = make_client(tmp_path, FakeServer(), mode="offline").model_info()
    assert info["model"] == "qwen3.5:2b-q4_K_M" and info["digest"] is None


def test_base_url_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/")
    client = OllamaClient(cache_dir=tmp_path, http_client=httpx.Client())
    assert client.base_url == "http://127.0.0.1:11434"


@pytest.mark.parametrize("task", ["..", ".", ".hidden", "-x", "Classify", ""])
def test_task_must_be_a_plain_folder_name(tmp_path, task):
    client = make_client(tmp_path, FakeServer())
    with pytest.raises(ValueError):
        client.chat(task=task, system="", user="", schema=SCHEMA)


def test_no_cache_mode_calls_every_time_and_writes_nothing(tmp_path):
    server = FakeServer()
    make_client(tmp_path, server).model_info()  # a saved copy exists, and is not used below
    chat(make_client(tmp_path, server))         # so does a cached reply for the same request
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    client = make_client(tmp_path, server, mode="no_cache")
    first, second = chat(client), chat(client)
    assert first.from_cache is False and second.from_cache is False
    assert server.calls("/api/chat") == 3
    assert client.model_info()["digest"] == "abc123"
    assert server.calls("/api/tags") == 2
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before

    empty = tmp_path / "never-created"
    fresh = make_client(empty, FakeServer(), mode="no_cache")
    chat(fresh)
    fresh.model_info()
    assert not empty.exists()


def test_no_cache_model_info_has_no_saved_fallback(tmp_path):
    make_client(tmp_path, FakeServer()).model_info()
    with pytest.raises(LLMError):
        make_client(tmp_path, FakeServer(fail_info=True), mode="no_cache").model_info()
