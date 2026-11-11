"""Time helpers.

Stored timestamps are UTC ISO 8601 strings with milliseconds, so they sort as text.
Calendar days used by counters ("day" columns) follow America/Sao_Paulo.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Clock = Callable[[], datetime]


def _sao_paulo() -> tzinfo:
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:  # image without a tz database; Brazil has had no DST since 2019
        return timezone(timedelta(hours=-3), "America/Sao_Paulo")


SAO_PAULO = _sao_paulo()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Format an aware datetime as UTC ISO 8601, e.g. 2026-11-11T13:05:00.123+00:00."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 timestamp; naive values are taken as UTC."""
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def sp_day(dt: datetime) -> str:
    """Calendar day in America/Sao_Paulo as YYYY-MM-DD."""
    return dt.astimezone(SAO_PAULO).date().isoformat()


def sp_day_start(day: str) -> datetime:
    """First instant of a Sao Paulo calendar day, as an aware datetime."""
    return datetime.combine(date.fromisoformat(day), time(0), tzinfo=SAO_PAULO)


def seconds_between(start: str, end: str) -> float:
    return (parse_iso(end) - parse_iso(start)).total_seconds()


def minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
