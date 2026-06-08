"""Google Calendar OAuth routes: status / start / callback.

Extracted from main.py (#29); uses the shared web_utils OAuth helpers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from companion_api.auth import get_current_user
from companion_api.google_calendar import (
    complete_google_calendar_oauth,
    google_calendar_status,
    start_google_calendar_oauth,
)
from companion_api.web_utils import oauth_result_page, public_url

router = APIRouter()


@router.get("/api/google-calendar/status/")
def google_calendar_connection_status(user: str = Depends(get_current_user)) -> dict[str, Any]:
    return google_calendar_status(user)


@router.get("/api/google-calendar/oauth/start")
def google_calendar_oauth_start(
    request: Request,
    user: str = Depends(get_current_user),
) -> dict[str, str]:
    try:
        return start_google_calendar_oauth(
            user_id=user,
            redirect_uri=public_url(request, "/api/google-calendar/oauth/callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/google-calendar/oauth/callback", response_class=HTMLResponse)
def google_calendar_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
):
    if not code or not state:
        return oauth_result_page(
            "Google Calendar connection failed",
            "Google did not return the expected authorization code.",
            ok=False,
        )
    try:
        complete_google_calendar_oauth(code=code, state=state)
        return oauth_result_page(
            "Google Calendar connected",
            "Companion can now create calendar events with email reminders.",
            ok=True,
        )
    except Exception as exc:
        return oauth_result_page(
            "Google Calendar connection failed",
            f"{type(exc).__name__}: {exc}",
            ok=False,
        )
