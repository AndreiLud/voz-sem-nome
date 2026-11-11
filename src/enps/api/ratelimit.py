"""In-memory rate limits and the distinct-submitter counter for the public API.

IP addresses never leave memory and are not even kept there in clear: every key is an
HMAC of the IP under a random key created at startup. Nothing here is written to disk.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from enps.storage.timeutil import Clock, utc_now

DAY_S = 86_400
HOUR_S = 3_600


@dataclass(frozen=True)
class Limits:
    per_ip_window_s: int
    per_ip_window_max: int
    per_ip_day_max: int
    global_hour_max: int

    @classmethod
    def from_settings(cls, cfg: Mapping[str, Any]) -> Limits:
        limits = cfg["limits"]
        return cls(limits["per_ip_window_s"], limits["per_ip_window_max"],
                   limits["per_ip_day_max"], limits["global_hour_max"])


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(scope)
        self.scope = scope              # "ip_window", "ip_day" or "global_hour"
        self.retry_after = retry_after  # seconds


def _hmac(key: bytes, ip: str) -> bytes:
    return hmac.new(key, ip.encode("utf-8"), hashlib.sha256).digest()


def _retry_after(hits: deque[float], window_s: int, limit: int, now: float) -> int | None:
    """Seconds until a slot frees up, or None when the window still has room."""
    inside = [t for t in hits if t > now - window_s]
    if len(inside) < limit:
        return None
    frees_at = inside[len(inside) - limit] + window_s
    return max(1, math.ceil(frees_at - now))


class RateLimiter:
    """Sliding windows: per IP (short window and 24 h) and global (1 h)."""

    def __init__(self, limits: Limits, clock: Clock = utc_now) -> None:
        self._limits = limits
        self._clock = clock
        self._key = secrets.token_bytes(32)
        self._per_ip: dict[bytes, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, ip: str) -> None:
        """Raise RateLimited when a new submission from `ip` would exceed a limit."""
        now = self._clock().timestamp()
        with self._lock:
            self._check(_hmac(self._key, ip), now)

    def record(self, ip: str) -> None:
        """Count one submission from `ip`, checking the limits again under the same lock.

        Concurrent requests can all pass `check` before any of them is recorded; the second
        check makes sure they still cannot exceed a limit together (raises RateLimited).
        """
        now = self._clock().timestamp()
        key = _hmac(self._key, ip)
        with self._lock:
            self._check(key, now)
            self._prune(now)
            self._per_ip.setdefault(key, deque()).append(now)
            self._global.append(now)

    def _check(self, key: bytes, now: float) -> None:
        lim = self._limits
        hits = self._per_ip.get(key, deque())
        checks = (
            ("ip_window", hits, lim.per_ip_window_s, lim.per_ip_window_max),
            ("ip_day", hits, DAY_S, lim.per_ip_day_max),
            ("global_hour", self._global, HOUR_S, lim.global_hour_max),
        )
        for scope, series, window_s, limit in checks:
            wait = _retry_after(series, window_s, limit, now)
            if wait is not None:
                raise RateLimited(scope, wait)

    def _prune(self, now: float) -> None:
        while self._global and self._global[0] <= now - HOUR_S:
            self._global.popleft()
        for key in list(self._per_ip):
            hits = self._per_ip[key]
            while hits and hits[0] <= now - DAY_S:
                hits.popleft()
            if not hits:
                del self._per_ip[key]


class SubmitterCounter:
    """Recognizes the first submission of an IP on a given day.

    Keeps HMAC-SHA256(ip, daily random salt) in memory only; the salt and the set are
    replaced when the day changes, so yesterday's hashes cannot be linked to today's.
    """

    def __init__(self) -> None:
        self._day: str | None = None
        self._salt = b""
        self._seen: set[bytes] = set()
        self._lock = threading.Lock()

    def is_new(self, ip: str, day: str) -> bool:
        with self._lock:
            if day != self._day:
                self._day = day
                self._salt = secrets.token_bytes(32)
                self._seen = set()
            digest = _hmac(self._salt, ip)
            if digest in self._seen:
                return False
            self._seen.add(digest)
            return True
