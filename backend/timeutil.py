"""Time helpers. Stored times are UTC ISO 8601; user-facing times are Europe/Warsaw."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")


def parse_ts(value: str | None, naive_tz: tzinfo = timezone.utc) -> datetime | None:
    """Parse ISO 8601 into an aware datetime, or None for empty or malformed input.

    Values without an offset (including date-only strings) are read in `naive_tz`:
    UTC for stored data, Warsaw for times the LLM derives from a user's request.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=naive_tz)


def fmt_local(iso: str | None, with_time: bool = True) -> str:
    dt = parse_ts(iso)
    if dt is None:
        return "brak danych"
    return dt.astimezone(WARSAW).strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
