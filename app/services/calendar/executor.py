"""Calendar domain agent: parse reminder text → Google Calendar event (same path as /api/reminders/google-calendar/)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from companion_api import settings
from companion_api.google_calendar import (
    create_calendar_event,
    google_calendar_configured,
    google_calendar_status,
)
from companion_api.reminder_parser import parse_reminder_message
from companion_api.reminders import ReminderScheduler


def execute_reminder_calendar(
    *,
    user_message: str,
    user_id: str,
    timezone_name: str | None = None,
    calendar_id: str = "primary",
    email_reminder_minutes: int = 0,
    popup_reminder_minutes: int | None = 10,
    store_reminder: bool = False,
) -> dict[str, Any]:
    """
    Run the Google Calendar reminder flow for an agent turn.

    Mirrors ``POST /api/reminders/google-calendar/`` without HTTP.
    """
    message = (user_message or "").strip()
    if not message:
        return {
            "ok": False,
            "error": "Reminder message is required.",
            "error_code": "invalid_input",
            "routed_providers": ["google_calendar"],
        }

    if not google_calendar_configured():
        return {
            "ok": False,
            "error": "Google Calendar OAuth is not configured on this server.",
            "error_code": "not_configured",
            "routed_providers": ["google_calendar"],
        }

    status = google_calendar_status(user_id)
    if not status.get("connected"):
        return {
            "ok": False,
            "error": "Google Calendar is not connected for this user.",
            "error_code": "not_connected",
            "routed_providers": ["google_calendar"],
            "calendar_status": status,
        }

    tz = timezone_name or settings.REMINDER_DEFAULT_TIMEZONE
    try:
        parsed = parse_reminder_message(message, timezone_name=tz)
        trigger = parsed["next_trigger"]
        if not isinstance(trigger, datetime):
            raise ValueError("Parsed reminder did not include a datetime.")
        title = str(parsed["title"])
        event = create_calendar_event(
            user_id=user_id,
            title=title,
            description=f"Created by Companion from: {message}",
            start=trigger,
            calendar_id=calendar_id,
            email_reminder_minutes=email_reminder_minutes,
            popup_reminder_minutes=popup_reminder_minutes,
        )
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": "parse_error",
            "routed_providers": ["google_calendar"],
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "error_code": "api_error",
            "routed_providers": ["google_calendar"],
        }

    reminder: dict[str, Any] | None = None
    if store_reminder:
        reminder = ReminderScheduler().create_reminder(
            {
                "member_id": user_id,
                "title": title,
                "description": (
                    f"Google Calendar event: {event.get('htmlLink') or event.get('id') or ''}"
                ),
                "type": "google_calendar",
                "schedule_type": "fixed",
                "next_trigger": trigger.isoformat(),
                "requires_confirmation": False,
                "created_by": user_id,
            }
        )

    return {
        "ok": True,
        "routed_providers": ["google_calendar"],
        "parsed": {
            "title": title,
            "next_trigger": trigger.isoformat(),
            "timezone": parsed.get("timezone"),
        },
        "calendar_event": {
            "id": event.get("id"),
            "html_link": event.get("htmlLink"),
            "summary": event.get("summary"),
        },
        "reminder": reminder,
    }
