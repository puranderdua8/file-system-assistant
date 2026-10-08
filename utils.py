"""Small, generic helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def iso_timestamp(ts: float) -> str:
    """Format a POSIX timestamp as a UTC ISO-8601 string (second precision)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
