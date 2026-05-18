"""Calendar domain agent: execution result → user-facing reply."""

from __future__ import annotations

from typing import Any


def reply_reminder_calendar(
    *,
    user_message: str,
    execution: dict[str, Any] | None,
    execute_tools: bool,
) -> str:
    """Template reply for calendar turns (no extra LLM)."""
    if not execute_tools:
        return (
            "I can add that to your Google Calendar — turn on tool execution "
            "or ask again with execute_tools enabled."
        )

    if not isinstance(execution, dict):
        return "I couldn't process that calendar request. Please try again."

    code = str(execution.get("error_code") or "").strip()
    if execution.get("ok"):
        parsed = execution.get("parsed") if isinstance(execution.get("parsed"), dict) else {}
        title = str(parsed.get("title") or "Reminder").strip()
        when = str(parsed.get("next_trigger") or "").strip()
        link = ""
        event = execution.get("calendar_event")
        if isinstance(event, dict):
            link = str(event.get("html_link") or "").strip()
        parts = [f'Added "{title}" to your Google Calendar']
        if when:
            parts.append(f"for {when}")
        msg = " ".join(parts) + "."
        if link:
            msg += f" Open: {link}"
        return msg

    err = str(execution.get("error") or "Calendar request failed.").strip()
    if code == "not_connected":
        return (
            f"{err} Connect Google Calendar first "
            "(GET /api/google-calendar/oauth/start), then try your reminder again."
        )
    if code == "not_configured":
        return f"{err} Ask an admin to configure Google Calendar OAuth for this deployment."
    if code == "parse_error":
        return (
            f"{err} Try phrasing like: "
            "'Remind me tomorrow at 9am to call the dentist' or "
            "'in 30 minutes to check the oven'."
        )
    return err
