"""Ingest of survey answers: validation, anonymization, deduplication and enqueueing.

Shared by the public API (site form), the internal API (Google Forms rows sent by n8n)
and the synthetic batch pipeline. Raw comments never reach the database: only the
anonymized text, its length and the counts of what was removed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import secrets
import sqlite3
import string
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import enps.anonymize as anonymizer
from enps import config
from enps.storage import db
from enps.storage.timeutil import SAO_PAULO, Clock, sp_day, to_iso, utc_now

TRACKING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0, O, 1, I or L
TRACKING_LENGTH = 8
PUBLIC_ID_ALPHABET = string.ascii_letters + string.digits + "-_"
PUBLIC_ID_LENGTH = 10
MIN_DEDUPE_CHARS = 15
LANGS = ("pt", "en")
CHANNEL_DATASET = {"site": "public", "google_forms": "public", "synthetic": "synthetic"}
MAX_CLOCK_SKEW = timedelta(minutes=10)
FORM_DATE_FORMAT = "%d/%m/%Y %H:%M:%S"  # Google Sheets timestamp in the pt-BR locale
SYNTHETIC_CSV = "data/synthetic/responses.csv"

_PUNCTUATION = re.compile(r"[^\w\s]|_")
_SPACES = re.compile(r"\s+")


class IngestError(Exception):
    """A submission that was not stored."""


class InvalidSubmission(IngestError):
    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("invalid submission")
        self.fields = fields


class DuplicateSubmission(IngestError):
    """Same anonymized comment already received in the duplicate window."""


@dataclass(frozen=True)
class Submission:
    score: int
    area: str
    tenure: str
    comment: str = ""
    lang: str = "pt"
    submitted_at: datetime | None = None


@dataclass(frozen=True)
class Accepted:
    response_id: int
    public_id: str
    status: str                       # "queued", or "skipped" when there is no comment
    priority: int
    comment_anon: str
    pii_counts: dict[str, int]
    tracking_code: str | None = None  # only for site submissions, shown once to the submitter


@dataclass(frozen=True)
class BatchReport:
    inserted: int
    already_present: int


# ---------------------------------------------------------------- field parsers


def _label_key(text: str) -> str:
    folded = unicodedata.normalize("NFD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _SPACES.sub(" ", folded.casefold()).strip()


def _choices(cfg: Mapping[str, Any], key: str) -> tuple[list[str], dict[str, str]]:
    items = cfg["survey"][key]
    codes = [item["code"] for item in items]
    labels = {_label_key(item["label"]["pt"]): item["code"] for item in items}
    return codes, labels


def _parse_score(value: Any, *, allow_text: bool) -> int:
    if allow_text and isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
        raise ValueError("must be an integer from 0 to 10")
    return value


def _parse_choice(value: Any, codes: list[str], labels: Mapping[str, str] | None) -> str:
    if isinstance(value, str):
        if value in codes:
            return value
        if labels and _label_key(value) in labels:
            return labels[_label_key(value)]
    raise ValueError("unknown value")


def _parse_comment(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("must be text")
    text = unicodedata.normalize("NFC", value).strip()
    if len(text) > max_chars:
        raise ValueError(f"must have at most {max_chars} characters")
    return text


def _parse_lang(value: Any) -> str:
    if value is None:
        return "pt"
    if value not in LANGS:
        raise ValueError("must be pt or en")
    return value


def _parse_consent(value: Any) -> bool:
    if value is not True:
        raise ValueError("must be true")
    return True


def _parse_timestamp(value: Any, now: datetime | None) -> datetime | None:
    """ISO 8601 or the Google Sheets pt-BR format; naive values are Sao Paulo time."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("must be a timestamp")
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        try:
            dt = datetime.strptime(value.strip(), FORM_DATE_FORMAT)
        except ValueError:
            raise ValueError("must be ISO 8601 or dd/mm/yyyy hh:mm:ss") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SAO_PAULO)
    if now is not None and dt > now + MAX_CLOCK_SKEW:
        raise ValueError("is in the future")
    return dt


def _collect(errors: dict[str, str], name: str, parse: Callable[[], Any]) -> Any:
    try:
        return parse()
    except ValueError as exc:
        errors[name] = str(exc)
        return None


def _validate(
    payload: Any, cfg: Mapping[str, Any], *, labels: bool, text_numbers: bool,
    consent: bool, timestamp: bool, now: datetime | None,
) -> Submission:
    if not isinstance(payload, Mapping):
        raise InvalidSubmission({"body": "must be a JSON object"})
    area_codes, area_labels = _choices(cfg, "areas")
    tenure_codes, tenure_labels = _choices(cfg, "tenure_bands")
    max_chars = cfg["privacy"]["max_comment_chars"]
    errors: dict[str, str] = {}
    values = {
        "score": _collect(errors, "score",
                          lambda: _parse_score(payload.get("score"), allow_text=text_numbers)),
        "area": _collect(errors, "area", lambda: _parse_choice(
            payload.get("area"), area_codes, area_labels if labels else None)),
        "tenure": _collect(errors, "tenure", lambda: _parse_choice(
            payload.get("tenure"), tenure_codes, tenure_labels if labels else None)),
        "comment": _collect(errors, "comment",
                            lambda: _parse_comment(payload.get("comment"), max_chars)),
        "lang": _collect(errors, "lang", lambda: _parse_lang(payload.get("lang"))),
    }
    if consent:
        _collect(errors, "consent", lambda: _parse_consent(payload.get("consent")))
    if timestamp:
        values["submitted_at"] = _collect(
            errors, "submitted_at", lambda: _parse_timestamp(payload.get("submitted_at"), now))
    if errors:
        raise InvalidSubmission(errors)
    return Submission(**values)


def validate_site(payload: Any, cfg: Mapping[str, Any] | None = None) -> Submission:
    """Site form: {score, area, tenure, comment, lang, consent} with codes only."""
    return _validate(payload, cfg or config.settings(), labels=False, text_numbers=False,
                     consent=True, timestamp=False, now=None)


def validate_form_row(
    payload: Any, cfg: Mapping[str, Any] | None = None, now: datetime | None = None
) -> Submission:
    """Google Forms row from n8n: codes or Portuguese labels, optional submitted_at."""
    return _validate(payload, cfg or config.settings(), labels=True, text_numbers=True,
                     consent=False, timestamp=True, now=now or utc_now())


def validate_synthetic_row(
    row: Mapping[str, Any], cfg: Mapping[str, Any] | None = None
) -> Submission:
    """Row of data/synthetic/responses.csv (all values are text)."""
    sub = _validate(row, cfg or config.settings(), labels=False, text_numbers=True,
                    consent=False, timestamp=True, now=None)
    if sub.submitted_at is None:
        raise InvalidSubmission({"submitted_at": "is required"})
    return sub


# ---------------------------------------------------------------- identifiers


def normalize_comment(text: str) -> str:
    """Lowercase, accents kept, punctuation removed, spaces collapsed."""
    return _SPACES.sub(" ", _PUNCTUATION.sub(" ", text.lower())).strip()


def dedupe_key(comment_anon: str) -> str | None:
    """sha256 of the normalized anonymized comment; None for short comments."""
    normalized = normalize_comment(comment_anon)
    if len(normalized) < MIN_DEDUPE_CHARS:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def new_tracking_code() -> str:
    raw = "".join(secrets.choice(TRACKING_ALPHABET) for _ in range(TRACKING_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_tracking_code(code: str) -> str | None:
    """Canonical form (8 characters, no separator) or None when it cannot be a code."""
    raw = re.sub(r"[\s-]", "", code).upper()
    if len(raw) != TRACKING_LENGTH or any(ch not in TRACKING_ALPHABET for ch in raw):
        return None
    return raw


def format_tracking_code(code: str) -> str:
    raw = normalize_tracking_code(code) or code
    return f"{raw[:4]}-{raw[4:]}"


def tracking_hash(code: str) -> str | None:
    raw = normalize_tracking_code(code)
    return hashlib.sha256(raw.encode("ascii")).hexdigest() if raw else None


def new_public_id() -> str:
    return "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(PUBLIC_ID_LENGTH))


# ---------------------------------------------------------------- ingest


def anonymize_comment(comment: str) -> tuple[str, dict[str, int], str]:
    """(anonymized text, counts by type, anonymizer version); empty comments skip the model."""
    if not comment:
        return "", {}, ""
    result = anonymizer.anonymize(comment)
    return result.text.strip(), dict(result.counts), result.version


def _insert_with_ids(
    conn: sqlite3.Connection, row: dict[str, Any], *, tracking: bool
) -> tuple[int, str, str | None]:
    for _ in range(5):
        code = new_tracking_code() if tracking else None
        candidate = {**row, "public_id": new_public_id(),
                     "tracking_hash": tracking_hash(code) if code else None}
        try:
            return db.insert_response(conn, candidate), candidate["public_id"], code
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" not in str(exc):
                raise
    raise RuntimeError("could not generate unique identifiers")


def ingest(
    conn: sqlite3.Connection,
    sub: Submission,
    *,
    channel: str,
    cfg: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    source_ref: str | None = None,
    reject_duplicates: bool = True,
) -> Accepted:
    """Anonymize, deduplicate and store one answer; queue it when it has a comment.

    Raises DuplicateSubmission (and counts it) when the same anonymized comment arrived
    in the same dataset within limits.duplicate_window_hours.
    """
    cfg = cfg or config.settings()
    now = now or utc_now()
    dataset = CHANNEL_DATASET[channel]
    comment_anon, pii_counts, version = anonymize_comment(sub.comment)
    key = dedupe_key(comment_anon)
    queued = bool(comment_anon)
    priority = cfg["queue"]["batch_priority" if channel == "synthetic" else "live_priority"]
    now_iso = to_iso(now)
    row = {
        "dataset": dataset, "channel": channel, "source_ref": source_ref,
        "submitted_at": to_iso(sub.submitted_at or now), "score": sub.score, "area": sub.area,
        "tenure": sub.tenure, "lang": sub.lang, "comment_anon": comment_anon,
        "comment_chars": len(sub.comment), "pii_counts": json.dumps(pii_counts, sort_keys=True),
        "anonymizer_version": version, "dedupe_key": key,
        "status": "queued" if queued else "skipped", "priority": priority,
        "queued_at": now_iso if queued else None, "finished_at": None if queued else now_iso,
    }
    day = sp_day(now)
    since = to_iso(now - timedelta(hours=cfg["limits"]["duplicate_window_hours"]))
    inserted: tuple[int, str, str | None] | None = None
    with db.transaction(conn):
        if reject_duplicates and key and db.has_recent_duplicate(conn, dataset, key, since):
            db.bump_daily(conn, day, dataset, channel, rejected_duplicate=1)
        else:
            inserted = _insert_with_ids(conn, row, tracking=channel == "site")
            db.bump_daily(conn, day, dataset, channel, submissions=1)
    if inserted is None:
        raise DuplicateSubmission()
    response_id, public_id, code = inserted
    return Accepted(response_id, public_id, row["status"], priority, comment_anon, pii_counts, code)


def ingest_synthetic_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any] | None = None,
    clock: Clock = utc_now,
) -> BatchReport:
    """Queue synthetic answers at batch priority; rows already present (by id) are skipped.

    Duplicates are not rejected here: the evaluation needs every row of the file.
    """
    cfg = cfg or config.settings()
    inserted = present = 0
    for row in rows:
        ref = row["id"]
        if db.source_ref_exists(conn, "synthetic", ref):
            present += 1
            continue
        try:
            sub = validate_synthetic_row(row, cfg)
        except InvalidSubmission as exc:
            raise ValueError(f"{ref}: {exc.fields}") from None
        ingest(conn, sub, channel="synthetic", cfg=cfg, now=clock(), source_ref=ref,
               reject_duplicates=False)
        inserted += 1
    return BatchReport(inserted=inserted, already_present=present)


def ingest_synthetic_csv(
    conn: sqlite3.Connection, path: str | Path | None = None, **kwargs: Any
) -> BatchReport:
    """Ingest data/synthetic/responses.csv (or `path`) into the synthetic dataset."""
    with open(config.resolve(path or SYNTHETIC_CSV), newline="", encoding="utf-8") as fh:
        return ingest_synthetic_rows(conn, csv.DictReader(fh), **kwargs)
