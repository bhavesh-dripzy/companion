"""CalendarProvider — create a Google Calendar reminder from text (HTTP provider).

Ports `app/services/calendar/executor.execute_reminder_calendar`, calling the
persistent `companion_api` calendar infra directly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ToolResult
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider

_TOOL = Tool(
    name="create_calendar_reminder",
    provider="calendar",
    description="Parse natural-language reminder text and create a Google Calendar event.",
    params={
        "type": "object",
        "properties": {
            "reminder_text": {
                "type": "string",
                "description": "e.g. 'remind me to call mom at 6pm'",
            }
        },
        "required": ["reminder_text"],
    },
    auth_mode=AuthMode.NONE,
    mutating=True,
    source="static",
)


def _create(message: str, user_id: str | None) -> dict[str, Any]:
    from companion_api import settings
    from companion_api.google_calendar import (
        create_calendar_event,
        google_calendar_configured,
        google_calendar_status,
    )
    from companion_api.reminder_parser import parse_reminder_message

    if not message:
        return {
            "ok": False,
            "error": "Reminder message is required.",
            "error_code": "invalid_input",
        }
    if not google_calendar_configured():
        return {
            "ok": False,
            "error": "Google Calendar is not configured.",
            "error_code": "not_configured",
        }
    if not google_calendar_status(user_id).get("connected"):
        return {
            "ok": False,
            "error": "Google Calendar is not connected.",
            "error_code": "not_connected",
        }
    try:
        parsed = parse_reminder_message(message, timezone_name=settings.REMINDER_DEFAULT_TIMEZONE)
        trigger = parsed["next_trigger"]
        if not isinstance(trigger, datetime):
            raise ValueError("Parsed reminder did not include a datetime.")
        title = str(parsed["title"])
        event = create_calendar_event(
            user_id=user_id,
            title=title,
            description=f"Created by Companion from: {message}",
            start=trigger,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "parse_error"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "error_code": "api_error"}
    return {
        "ok": True,
        "parsed": {"title": title, "next_trigger": trigger.isoformat()},
        "calendar_event": {
            "id": event.get("id"),
            "html_link": event.get("htmlLink"),
            "summary": event.get("summary"),
        },
    }


class CalendarProvider(HttpProvider):
    id = "calendar"
    label = "Google Calendar"
    auth_mode = AuthMode.NONE
    session_cls = None

    def declared_tools(self) -> list[Tool]:
        return [_TOOL]

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        if call.tool != "create_calendar_reminder":
            raise InvalidArgsError(f"unknown calendar tool: {call.tool}")
        text = str(call.arguments.get("reminder_text") or "").strip()
        if not text:
            raise InvalidArgsError("create_calendar_reminder needs reminder_text")
        result = await asyncio.to_thread(_create, text, ctx.user_id)
        ok = bool(result.get("ok"))
        return ToolResult(
            tool=call.tool,
            provider="calendar",
            ok=ok,
            arguments=dict(call.arguments),
            data=result,
            error=None if ok else ErrorCode.UPSTREAM_ERROR,
            error_detail=None if ok else str(result.get("error") or ""),
        )

    def build_attachments(self, results: list[ToolResult]) -> list[Attachment]:
        cards = []
        for result in results:
            event = (result.data or {}).get("calendar_event") if result.ok else None
            if event:
                cards.append(Attachment(kind="calendar_event_card", **event))
        return cards
