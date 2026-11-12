"""Local preview of the static site with the same routing as Cloudflare static assets.

    .venv/bin/python scripts/dev_server.py [--port 8787] [--mock] [--mock-public normal|small]
                                           [--mock-snapshot] [--no-rate-limit]

Serves site/public on 127.0.0.1 only:
  * directory index, trailing slash redirects (307) and "/page.html" -> "/page";
  * 404 with the nearest 404.html (/en/... uses en/404.html);
  * headers from site/public/_headers (without HSTS and upgrade-insecure-requests, so
    plain http on localhost works).

/api/v1/public/* is proxied to the local mode's public API at http://127.0.0.1:8001/v1/public/*
with X-Origin-Secret (from .env) and X-Client-IP 127.0.0.1, or, with --mock, answered from
site/src/fixtures/:
  GET  /dashboard?dataset=synthetic|public    fixture dashboards
  GET  /status                                the flat status of the live demo's Worker
  POST /submissions                           202 with a tracking code; the job goes
                                              queued -> processing -> done in about 12 s
  GET  /submissions/{code}                    state of a job; ready made codes:
       TEST-DQNE done, TEST-CR7T critical (with burnout), TEST-SK9P skipped,
       TEST-FA9D failed, TEST-WA7T always queued
Mock triggers in the comment: a repeated comment gives 409, "#409", "#429" and "#503"
force those answers ("#quota" gives the Worker's 503 when the model's daily quota is used
up), words such as "grita" or "remédio" give a critical result.
More than the configured submissions per IP window gives 429 (unless --no-rate-limit).
/__og/pt and /__og/en render the social card used to make /assets/og/og-*.png.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import mimetypes
import posixpath
import re
import secrets
import sys
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from enps import config  # noqa: E402

PUBLIC_DIR = ROOT / "site" / "public"
FIXTURES_DIR = ROOT / "site" / "src" / "fixtures"
API_PREFIX = "/api/v1/public"
UPSTREAM_PREFIX = "/v1/public"  # what enps.api.public serves
UPSTREAM = ("127.0.0.1", 8001)
HIDDEN_FILES = {"_headers", "_redirects", ".assetsignore"}
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".svg": "image/svg+xml", ".woff2": "font/woff2",
         ".xml": "application/xml", ".txt": "text/plain", ".html": "text/html",
         ".png": "image/png", ".webmanifest": "application/manifest+json"}


# ------------------------------------------------------------------ routing


@dataclass(frozen=True)
class Route:
    status: int
    file: Path | None = None
    location: str | None = None


def _is_file(root: Path, rel: str) -> bool:
    path = (root / rel.lstrip("/")).resolve()
    return path.is_file() and (path == root or root in path.parents)


def _not_found(root: Path, path: str) -> Route:
    """Nearest 404.html walking up from the requested folder."""
    folder = posixpath.dirname(path) if not path.endswith("/") else path.rstrip("/")
    while True:
        candidate = posixpath.join(folder, "404.html")
        if _is_file(root, candidate):
            return Route(404, root / candidate.lstrip("/"))
        if folder in ("", "/"):
            return Route(404)
        folder = posixpath.dirname(folder)


def resolve(root: Path, raw_path: str, query: str = "") -> Route:
    """Cloudflare static assets rules: html_handling auto-trailing-slash, 404-page."""
    path = posixpath.normpath(unquote(raw_path)) + ("/" if raw_path.endswith("/") and raw_path != "/" else "")
    if not path.startswith("/") or ".." in path.split("/"):
        return Route(400)
    suffix = f"?{query}" if query else ""
    name = posixpath.basename(path)
    if name in HIDDEN_FILES:
        return _not_found(root, path)
    if name == "index.html" and _is_file(root, path):
        return Route(307, location=path[: -len("index.html")] + suffix)
    if path.endswith(".html") and _is_file(root, path):
        return Route(307, location=path[: -len(".html")] + suffix)
    if path.endswith("/"):
        if _is_file(root, path + "index.html"):
            return Route(200, root / (path + "index.html").lstrip("/"))
        if path != "/" and _is_file(root, path.rstrip("/") + ".html"):
            return Route(307, location=path.rstrip("/") + suffix)
        return _not_found(root, path)
    if _is_file(root, path):
        return Route(200, root / path.lstrip("/"))
    if _is_file(root, path + ".html"):
        return Route(200, root / (path + ".html").lstrip("/"))
    if _is_file(root, path + "/index.html"):
        return Route(307, location=path + "/" + suffix)
    return _not_found(root, path)


# ------------------------------------------------------------------ _headers


def parse_headers_file(text: str) -> list[tuple[str, dict[str, str]]]:
    rules: list[tuple[str, dict[str, str]]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            rules.append((line.strip(), {}))
        elif rules and ":" in line:
            name, _, value = line.strip().partition(":")
            rules[-1][1][name.strip()] = value.strip()
    return rules


def _pattern_matches(pattern: str, path: str) -> bool:
    regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return re.match(regex, path) is not None


def headers_for(rules: list[tuple[str, dict[str, str]]], path: str) -> dict[str, str]:
    """Matching headers, adapted to plain http on localhost."""
    out: dict[str, str] = {}
    for pattern, headers in rules:
        if _pattern_matches(pattern, path):
            out.update(headers)
    out.pop("Strict-Transport-Security", None)
    if "Content-Security-Policy" in out:
        directives = [d for d in out["Content-Security-Policy"].split(";")
                      if d.strip() != "upgrade-insecure-requests"]
        out["Content-Security-Policy"] = ";".join(directives).strip()
    return out


# ------------------------------------------------------------------ mock API


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


CRITICAL_WORDS = re.compile(
    r"assedi|grita|humilh|ameac|nao aguento|nao quero mais viver|remedio|burnout|panico|"
    r"discrimin|piada|bate|agred")
TRACKING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PRESET_CODES = {
    "TESTDQNE": "submission-done.json",
    "TESTCR7T": "submission-critical.json",
    "TESTSK9P": "submission-skipped.json",
    "TESTFA9D": "submission-failed.json",
}
WAITING_CODE = "TESTWA7T"
QUEUED_S, DONE_S = 4.0, 12.0


@dataclass
class MockJob:
    created: float
    comment_anon: str
    pii_counts: dict[str, int]
    critical: bool
    skipped: bool


@dataclass
class MockApi:
    """In-memory imitation of the public API, driven by a clock for tests."""
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    public_variant: str = "normal"
    snapshot: bool = False
    rate_limit: bool = True
    jobs: dict[str, MockJob] = field(default_factory=dict)
    recent: list[float] = field(default_factory=list)
    seen: dict[str, float] = field(default_factory=dict)

    def handle(self, method: str, path: str, query: str, body: bytes) -> tuple[int, dict[str, Any], dict[str, str]]:
        sub = path[len(API_PREFIX):] or "/"
        params = parse_qs(query)
        if method == "GET" and sub == "/dashboard":
            return self.dashboard(params.get("dataset", ["synthetic"])[0])
        if method == "GET" and sub == "/status":
            return self.status()
        if method == "POST" and sub == "/submissions":
            return self.submit(body)
        if method == "GET" and sub.startswith("/submissions/"):
            return self.lookup(sub.rsplit("/", 1)[1])
        return 404, {"error": "not_found"}, {}

    # endpoints ----------------------------------------------------------

    def _snapshot_headers(self) -> dict[str, str]:
        if not self.snapshot:
            return {"X-Data-Source": "live"}
        return {"X-Data-Source": "snapshot", "X-Snapshot-At": "2026-11-10T14:30:00.000+00:00"}

    def dashboard(self, dataset: str) -> tuple[int, dict[str, Any], dict[str, str]]:
        if dataset == "synthetic":
            return 200, _fixture("dashboard-synthetic.json"), self._snapshot_headers()
        if dataset == "public":
            name = "dashboard-public-small.json" if self.public_variant == "small" else "dashboard-public.json"
            return 200, _fixture(name), self._snapshot_headers()
        return 422, {"error": "validation", "fields": {"dataset": "must be synthetic or public"}}, {}

    def status(self) -> tuple[int, dict[str, Any], dict[str, str]]:
        data = _without_sample(_fixture("status.json"))
        active = [j for j in self.jobs.values() if self.clock() - j.created < DONE_S and not j.skipped]
        data["pending"] = data["pending"] + len(active)
        data["generated_at"] = _iso(self.wall())
        return 200, data, self._snapshot_headers()

    def submit(self, body: bytes) -> tuple[int, dict[str, Any], dict[str, str]]:
        try:
            payload = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 422, {"error": "validation", "fields": {"body": "must be a JSON object"}}, {}
        if not isinstance(payload, dict):
            return 422, {"error": "validation", "fields": {"body": "must be a JSON object"}}, {}
        fields = validate(payload)
        if fields:
            return 422, {"error": "validation", "fields": fields}, {}
        if not isinstance(payload.get("turnstile"), str) or not payload["turnstile"]:
            return 403, {"error": "turnstile"}, {}
        comment = (payload.get("comment") or "").strip()
        forced = self._forced(comment)
        if forced:
            return forced
        limited = self._rate_limited()
        if limited:
            return limited
        key = _fold(" ".join(comment.split()))
        if len(key) >= 15 and key in self.seen:
            return 409, {"error": "duplicate"}, {}
        if len(key) >= 15:
            self.seen[key] = self.clock()
        return self._accept(comment)

    def lookup(self, raw_code: str) -> tuple[int, dict[str, Any], dict[str, str]]:
        code = normalize_code(raw_code)
        if code is None:
            return 404, {"error": "not_found"}, {}
        shown = f"{code[:4]}-{code[4:]}"
        if code in PRESET_CODES:
            return 200, {"code": shown, **_as_api(_fixture(PRESET_CODES[code]))}, {}
        if code == WAITING_CODE:
            return 200, {"code": shown, "status": "queued", "comment_anon": "", "pii_counts": {},
                         "queue_position": 3, "eta_seconds": 36}, {}
        job = self.jobs.get(code)
        if job is None:
            return 404, {"error": "not_found"}, {}
        return 200, {"code": shown, **self._job_state(job)}, {}

    # helpers -----------------------------------------------------------

    def _forced(self, comment: str) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        if "#409" in comment:
            return 409, {"error": "duplicate"}, {}
        if "#429" in comment:
            return 429, {"error": "rate_limited", "retry_after": 240}, {"Retry-After": "240"}
        if "#503" in comment:
            return 503, {"error": "queue_full"}, {"Retry-After": "120"}
        if "#quota" in comment:
            return 503, {"error": "quota"}, {"Retry-After": "3600"}
        return None

    def _rate_limited(self) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        limits = config.settings()["limits"]
        now = self.clock()
        self.recent = [t for t in self.recent if now - t < limits["per_ip_window_s"]]
        if self.rate_limit and len(self.recent) >= limits["per_ip_window_max"]:
            retry = int(limits["per_ip_window_s"] - (now - self.recent[0])) + 1
            return 429, {"error": "rate_limited", "retry_after": retry}, {"Retry-After": str(retry)}
        self.recent.append(now)
        return None

    def _accept(self, comment: str) -> tuple[int, dict[str, Any], dict[str, str]]:
        code = "".join(secrets.choice(TRACKING_ALPHABET) for _ in range(8))
        text, counts = mock_anonymize(comment)
        job = MockJob(created=self.clock(), comment_anon=text,
                      pii_counts=counts, critical=bool(CRITICAL_WORDS.search(_fold(comment))),
                      skipped=not comment)
        self.jobs[code] = job
        return 202, {"code": f"{code[:4]}-{code[4:]}", **self._job_state(job)}, {}

    def _job_state(self, job: MockJob) -> dict[str, Any]:
        """Same shape as enps.api.public: queue fields while waiting, result fields when done."""
        base = {"comment_anon": job.comment_anon, "pii_counts": job.pii_counts}
        if job.skipped:
            return {"status": "skipped", **base}
        elapsed = self.clock() - job.created
        if elapsed < QUEUED_S:
            return {"status": "queued", **base, "queue_position": 2 if elapsed < QUEUED_S / 2 else 1,
                    "eta_seconds": int(DONE_S - elapsed)}
        if elapsed < DONE_S:
            return {"status": "processing", **base, "queue_position": None,
                    "eta_seconds": int(DONE_S - elapsed)}
        done = _fixture("submission-critical.json" if job.critical else "submission-done.json")
        return {"status": "done", **base, **done["result"]}


def validate(payload: Mapping[str, Any]) -> dict[str, str]:
    """Field errors, with the same rules as the real ingest."""
    settings = config.settings()
    errors: dict[str, str] = {}
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 10:
        errors["score"] = "must be an integer from 0 to 10"
    if payload.get("area") not in config.area_codes():
        errors["area"] = "unknown value"
    if payload.get("tenure") not in config.tenure_codes():
        errors["tenure"] = "unknown value"
    comment = payload.get("comment")
    if comment is not None and not isinstance(comment, str):
        errors["comment"] = "must be text"
    elif isinstance(comment, str) and len(comment.strip()) > settings["privacy"]["max_comment_chars"]:
        errors["comment"] = f"must have at most {settings['privacy']['max_comment_chars']} characters"
    if payload.get("consent") is not True:
        errors["consent"] = "must be true"
    if payload.get("lang", "pt") not in ("pt", "en"):
        errors["lang"] = "must be pt or en"
    return errors


def normalize_code(code: str) -> str | None:
    raw = re.sub(r"[\s-]", "", unquote(code)).upper()
    if len(raw) != 8 or any(ch not in TRACKING_ALPHABET for ch in raw):
        return None
    return raw


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DIGITS = re.compile(r"\+?\d[\d .()/-]{6,}\d")


def mock_anonymize(text: str) -> tuple[str, dict[str, int]]:
    """Real anonymizer when it is available, else a rough stand-in for emails and numbers."""
    if not text:
        return "", {}
    try:
        from enps.anonymize import anonymize
        result = anonymize(text)
        return result.text, dict(result.counts)
    except Exception:  # the anonymizer may not be built yet; this is only a preview
        counts: dict[str, int] = {}

        def mask(kind: str) -> Callable[[re.Match[str]], str]:
            def sub(_: re.Match[str]) -> str:
                counts[kind] = counts.get(kind, 0) + 1
                return f"[{kind}]"
            return sub

        text = _EMAIL.sub(mask("EMAIL"), text)
        text = _DIGITS.sub(mask("DOCUMENTO"), text)
        return text, counts


def _without_sample(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != "sample"}


def _as_api(fixture: dict[str, Any]) -> dict[str, Any]:
    """A submission fixture in the API's shape: the result fields at the top level."""
    body = {k: v for k, v in fixture.items() if k in ("status", "comment_anon", "pii_counts")}
    return {**body, **(fixture.get("result") or {})}


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000+00:00", time.gmtime(epoch))


# ------------------------------------------------------------------ proxy


def read_env_secret(path: Path = ROOT / ".env", name: str = "ORIGIN_SHARED_SECRET") -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def upstream_target(path: str, query: str = "") -> str:
    """Browser path /api/v1/public/x -> API path /v1/public/x, query kept."""
    target = UPSTREAM_PREFIX + path[len(API_PREFIX):]
    return target + (f"?{query}" if query else "")


def proxy(method: str, target: str, body: bytes, headers: Mapping[str, str],
          secret: str) -> tuple[int, bytes, list[tuple[str, str]]]:
    forward = {k: v for k, v in headers.items() if k.lower() in ("content-type", "accept", "accept-language")}
    forward.update({"X-Origin-Secret": secret, "X-Client-IP": "127.0.0.1"})
    conn = http.client.HTTPConnection(*UPSTREAM, timeout=30)
    try:
        conn.request(method, target, body=body or None, headers=forward)
        response = conn.getresponse()
        keep = [(k, v) for k, v in response.getheaders()
                if k.lower() in ("content-type", "retry-after", "x-data-source", "x-snapshot-at", "cache-control")]
        return response.status, response.read(), keep
    except OSError:
        payload = json.dumps({"error": "offline"}).encode()
        return 503, payload, [("Content-Type", "application/json")]
    finally:
        conn.close()


# ------------------------------------------------------------------ server


def render_og(lang: str) -> str:
    from enps.export.site_og import render_card
    return render_card(lang)


class Handler(BaseHTTPRequestHandler):
    server_version = "vsn-dev"
    root: Path = PUBLIC_DIR
    mock: MockApi | None = None
    secret: str = ""
    header_rules: list[tuple[str, dict[str, str]]] = []

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(head=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(head=True)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(head=False)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def _dispatch(self, head: bool) -> None:
        url = urlsplit(self.path)
        if url.path.startswith(API_PREFIX + "/") or url.path == API_PREFIX:
            self._api(url.path, url.query)
            return
        if url.path in ("/__og/pt", "/__og/en") and self.command == "GET":
            self._send(200, render_og(url.path[-2:]).encode(), "text/html; charset=utf-8", url.path)
            return
        if self.command == "POST":
            self._send(405, b"", "text/plain", url.path)
            return
        self._static(url.path, url.query, head)

    def _api(self, path: str, query: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.mock is not None:
            status, data, extra = self.mock.handle(self.command, path, query, body)
            payload = json.dumps(data, ensure_ascii=False).encode()
            headers = [("Content-Type", "application/json"), ("Cache-Control", "no-store"), *extra.items()]
        else:
            status, payload, headers = proxy(self.command, upstream_target(path, query), body,
                                             dict(self.headers), self.secret)
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _static(self, path: str, query: str, head: bool) -> None:
        route = resolve(self.root, path, query)
        if route.location:
            self.send_response(route.status)
            self.send_header("Location", route.location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if route.file is None:
            self._send(route.status, b"Not found", "text/plain; charset=utf-8", path, head)
            return
        ctype = TYPES.get(route.file.suffix) or mimetypes.guess_type(route.file.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/xml", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(route.status, route.file.read_bytes(), ctype, path, head)

    def _send(self, status: int, body: bytes, ctype: str, path: str, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", '"' + hashlib.sha1(body).hexdigest()[:16] + '"')
        if not path.startswith("/__og/"):  # dev-only card page, styled inline
            for name, value in headers_for(self.header_rules, path).items():
                self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local preview of site/public.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--mock", action="store_true", help="answer the API from fixtures")
    parser.add_argument("--mock-public", choices=("normal", "small"), default="normal",
                        help="public dashboard fixture: enough answers, or fewer than k")
    parser.add_argument("--mock-snapshot", action="store_true",
                        help="mark dashboard and status answers as a Worker snapshot")
    parser.add_argument("--no-rate-limit", action="store_true", help="mock: never answer 429 by count")
    parser.add_argument("--root", type=Path, default=PUBLIC_DIR)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    headers_file = root / "_headers"
    Handler.root = root
    Handler.header_rules = parse_headers_file(headers_file.read_text(encoding="utf-8")) if headers_file.exists() else []
    if args.mock:
        Handler.mock = MockApi(public_variant=args.mock_public, snapshot=args.mock_snapshot,
                               rate_limit=not args.no_rate_limit)
    else:
        Handler.secret = read_env_secret()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    mode = "mock API" if args.mock else f"API proxied to http://{UPSTREAM[0]}:{UPSTREAM[1]}"
    print(f"serving {root} on http://127.0.0.1:{args.port}/ ({mode})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
