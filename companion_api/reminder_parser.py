from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def parse_reminder_message(message: str, *, timezone_name: str, now: datetime | None = None) -> dict[str, object]:
    text = " ".join(message.strip().split())
    if not text:
        raise ValueError("Reminder message is required.")
    tz = ZoneInfo(timezone_name)
    current = now.astimezone(tz) if now else datetime.now(tz)
    lowered = text.lower()

    title = _extract_title(text)
    trigger = _parse_relative(lowered, current) or _parse_named_day(lowered, current) or _parse_isoish(lowered, current)
    if trigger is None:
        raise ValueError("Could not find a reminder time. Try 'tomorrow at 9am' or 'in 30 minutes'.")
    return {"title": title, "next_trigger": trigger, "timezone": timezone_name}


def _extract_title(text: str) -> str:
    cleaned = re.sub(r"^remind\s+me\s+(?:to|about)?\s*", "", text, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^reminder\s+(?:to|about)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.split(
        r"\s+(?:tomorrow|today|tonight|in\s+\d+\s+(?:minutes?|mins?|hours?|hrs?|days?)|on\s+\d{4}-\d{1,2}-\d{1,2}|at\s+\d{1,2})\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,-")
    return cleaned or text


def _parse_relative(lowered: str, current: datetime) -> datetime | None:
    match = re.search(r"\bin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?|days?)\b", lowered)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith(("minute", "min")):
        return current + timedelta(minutes=amount)
    if unit.startswith(("hour", "hr")):
        return current + timedelta(hours=amount)
    return current + timedelta(days=amount)


def _parse_named_day(lowered: str, current: datetime) -> datetime | None:
    if "tomorrow" in lowered:
        base = current.date() + timedelta(days=1)
    elif "today" in lowered or "tonight" in lowered:
        base = current.date()
    else:
        return None
    hour, minute = _parse_time(lowered, default_hour=20 if "tonight" in lowered else 9)
    trigger = datetime(base.year, base.month, base.day, hour, minute, tzinfo=current.tzinfo)
    if trigger <= current:
        trigger += timedelta(days=1)
    return trigger


def _parse_isoish(lowered: str, current: datetime) -> datetime | None:
    match = re.search(r"\bon\s+(\d{4})-(\d{1,2})-(\d{1,2})", lowered)
    if not match:
        return None
    year, month, day = (int(match.group(i)) for i in range(1, 4))
    hour, minute = _parse_time(lowered, default_hour=9)
    return datetime(year, month, day, hour, minute, tzinfo=current.tzinfo)


def _parse_time(lowered: str, *, default_hour: int) -> tuple[int, int]:
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if not match:
        return default_hour, 0
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix == "pm" and hour != 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    return hour, minute
