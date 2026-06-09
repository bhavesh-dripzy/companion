"""Reminder routes: create / google-calendar / pending / respond.

Extracted verbatim from main.py (#29). The EventBridge target
`scheduled_reminder_handler` stays in main.py (it is a Lambda handler, not a route).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from companion_api.auth import get_current_user
from companion_api.dynamodb import ReminderRepository
from companion_api.models import (
    GoogleCalendarReminderRequest,
    ReminderCreateRequest,
    ReminderResponseRequest,
)
from companion_api.reminder_calendar import execute_reminder_calendar
from companion_api.reminders import ReminderScheduler

router = APIRouter()


@router.post("/api/reminders/")
def create_reminder(
    payload: ReminderCreateRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    reminder = ReminderScheduler().create_reminder(
        {
            **payload.model_dump(),
            "member_id": payload.member_id,
            "next_trigger": payload.next_trigger.isoformat(),
        }
    )
    return {"status": "success", "reminder": reminder}


@router.post("/api/reminders/google-calendar/")
def create_google_calendar_reminder(
    payload: GoogleCalendarReminderRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    result = execute_reminder_calendar(
        user_message=payload.message,
        user_id=user,
        timezone_name=payload.timezone,
        calendar_id=payload.calendar_id,
        email_reminder_minutes=payload.email_reminder_minutes,
        popup_reminder_minutes=payload.popup_reminder_minutes,
        store_reminder=payload.store_reminder,
    )
    if not result.get("ok"):
        code = str(result.get("error_code") or "")
        detail = str(result.get("error") or "Calendar request failed.")
        if code in ("parse_error", "invalid_input", "not_connected", "not_configured"):
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=502, detail=detail)
    return {
        "status": "success",
        "parsed": result.get("parsed"),
        "calendar_event": result.get("calendar_event"),
        "reminder": result.get("reminder"),
    }


@router.get("/api/reminders/pending/")
def pending_reminders(
    member_id: str = Query(min_length=1),
    _user: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    reminders = ReminderRepository().list_pending(member_id)
    return [
        {
            "id": r["reminder_id"],
            "reminder_id": r["reminder_id"],
            "member_id": r["member_id"],
            "message": r.get("title", ""),
            "description": r.get("description", ""),
            "next_trigger": r.get("next_trigger"),
        }
        for r in reminders
    ]


@router.post("/api/reminders/respond/")
def respond_reminder(
    payload: ReminderResponseRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        reminder = ReminderScheduler().respond(
            member_id=payload.member_id,
            reminder_id=payload.reminder_id,
            response=payload.response,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"status": "success", "reminder_id": reminder["reminder_id"]}
