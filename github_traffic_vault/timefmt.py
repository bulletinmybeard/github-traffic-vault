"""Render stored-UTC datetimes in the configured display timezone."""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo


def to_local(value: datetime, tz: tzinfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz)


def format_local(value: datetime, tz: tzinfo, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return to_local(value, tz).strftime(fmt)
