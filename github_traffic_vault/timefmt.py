"""Render stored-UTC datetimes in the configured display timezone."""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo


def today_in_tz(tz: tzinfo) -> date:
    """Calendar date for ``tz`` right now (matches the web UI period picker)."""
    return datetime.now(UTC).astimezone(tz).date()


def traffic_today_utc() -> date:
    """GitHub daily traffic buckets are keyed by UTC calendar day."""
    return datetime.now(UTC).date()


def to_local(value: datetime, tz: tzinfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz)


def format_local(value: datetime, tz: tzinfo, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return to_local(value, tz).strftime(fmt)
