"""The pipeline's clock — current IST date-time injected into prompts.

LLMs have no clock: a planner asked for "flights tomorrow" or a hotel search
with unstated dates fills date arguments from its training-era prior (a past
year), and date-validated upstreams reject the call ("check-in date cannot be
in the past"). Every planner and the narrator get this one line so relative
dates resolve against the real now. IST because the product operates in India;
falls back to a fixed +05:30 offset if the tz database is unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone


def now_ist() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        return datetime.now(UTC).astimezone(timezone(timedelta(hours=5, minutes=30)))


def clock_line() -> str:
    """One prompt line pinning 'now' for relative-date resolution."""
    now = now_ist()
    return (
        f"(current date-time: {now.strftime('%A, %d %B %Y, %I:%M %p')} IST — "
        f"today is {now.strftime('%Y-%m-%d')}; resolve relative dates like "
        '"today"/"tomorrow"/"next Friday" against this, never a past date)'
    )
