from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode

import boto3
import requests

from companion_api import settings
from companion_api.secrets import secret_or_env

_SECRET_ID = "companion/google-calendar-tokens"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
_PENDING_TTL_SECONDS = 15 * 60


def _client() -> Any:
    return boto3.client("secretsmanager", region_name=settings.AWS_REGION)


def _secret_id() -> str:
    return secret_or_env("GOOGLE_CALENDAR_TOKEN_SECRET_ID", _SECRET_ID)


def _root_secret() -> dict[str, Any]:
    try:
        response = _client().get_secret_value(SecretId=_secret_id())
        parsed = json.loads(response.get("SecretString") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _write_root_secret(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":"))
    client = _client()
    try:
        client.put_secret_value(SecretId=_secret_id(), SecretString=raw)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(
            Name=_secret_id(),
            SecretString=raw,
            Description="Google Calendar OAuth tokens for Companion users",
        )


def _user_block(payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    users = payload.get("users")
    if not isinstance(users, dict):
        users = {}
        payload["users"] = users
    block = users.get(user_id)
    if not isinstance(block, dict):
        block = {}
        users[user_id] = block
    return block


def _credentials() -> tuple[str, str]:
    client_id = secret_or_env("GOOGLE_CALENDAR_CLIENT_ID")
    client_secret = secret_or_env("GOOGLE_CALENDAR_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Google Calendar OAuth client is not configured.")
    return client_id, client_secret


def google_calendar_configured() -> bool:
    return bool(secret_or_env("GOOGLE_CALENDAR_CLIENT_ID") and secret_or_env("GOOGLE_CALENDAR_CLIENT_SECRET"))


def start_google_calendar_oauth(*, user_id: str, redirect_uri: str) -> dict[str, str]:
    client_id, _client_secret = _credentials()
    state = secrets.token_urlsafe(32)
    payload = _root_secret()
    pending = payload.get("oauth_pending")
    if not isinstance(pending, dict):
        pending = {}
    now = int(time.time())
    pending = {
        key: value
        for key, value in pending.items()
        if isinstance(value, dict) and now - int(value.get("created_at", 0)) < _PENDING_TTL_SECONDS
    }
    pending[state] = {"user_id": user_id, "redirect_uri": redirect_uri, "created_at": now}
    payload["oauth_pending"] = pending
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_root_secret(payload)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {"authorization_url": f"{_AUTH_URL}?{urlencode(params)}", "state": state}


def complete_google_calendar_oauth(*, code: str, state: str) -> dict[str, Any]:
    client_id, client_secret = _credentials()
    payload = _root_secret()
    pending = payload.get("oauth_pending")
    if not isinstance(pending, dict) or state not in pending:
        raise RuntimeError("Google Calendar OAuth state was not recognized. Start connection again.")
    session = pending.pop(state)
    if int(time.time()) - int(session.get("created_at", 0)) > _PENDING_TTL_SECONDS:
        payload["oauth_pending"] = pending
        _write_root_secret(payload)
        raise RuntimeError("Google Calendar OAuth session expired. Start connection again.")

    redirect_uri = str(session["redirect_uri"])
    token_response = requests.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if not token_response.ok:
        raise RuntimeError(f"Google token exchange failed with HTTP {token_response.status_code}: {token_response.text[:300]}")
    tokens = token_response.json()
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise RuntimeError("Google token exchange did not return an access token.")
    if tokens.get("expires_in"):
        tokens["expires_at"] = int(time.time()) + int(tokens["expires_in"])

    user_id = str(session["user_id"])
    block = _user_block(payload, user_id)
    block["tokens"] = tokens
    block["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["oauth_pending"] = pending
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_root_secret(payload)
    return {"status": "connected", "user_id": user_id}


def google_calendar_status(user_id: str) -> dict[str, Any]:
    tokens = ((_root_secret().get("users") or {}).get(user_id) or {}).get("tokens")
    connected = isinstance(tokens, dict) and bool(tokens.get("refresh_token") or tokens.get("access_token"))
    return {"configured": google_calendar_configured(), "connected": connected}


def _tokens_for_user(user_id: str) -> dict[str, Any]:
    tokens = ((_root_secret().get("users") or {}).get(user_id) or {}).get("tokens")
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise RuntimeError("Google Calendar is not connected for this user.")
    return tokens


def _access_token(user_id: str) -> str:
    tokens = _tokens_for_user(user_id)
    expires_at = int(tokens.get("expires_at") or 0)
    if expires_at - 60 > int(time.time()) or not tokens.get("refresh_token"):
        return str(tokens["access_token"])

    client_id, client_secret = _credentials()
    response = requests.post(
        _TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Google token refresh failed with HTTP {response.status_code}: {response.text[:300]}")
    refreshed = response.json()
    tokens.update(refreshed)
    if refreshed.get("expires_in"):
        tokens["expires_at"] = int(time.time()) + int(refreshed["expires_in"])

    payload = _root_secret()
    _user_block(payload, user_id)["tokens"] = tokens
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_root_secret(payload)
    return str(tokens["access_token"])


def create_calendar_event(
    *,
    user_id: str,
    title: str,
    description: str,
    start: datetime,
    calendar_id: str = "primary",
    email_reminder_minutes: int = 0,
    popup_reminder_minutes: int | None = 10,
) -> dict[str, Any]:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    overrides: list[dict[str, Any]] = [{"method": "email", "minutes": max(0, int(email_reminder_minutes))}]
    if popup_reminder_minutes is not None:
        overrides.append({"method": "popup", "minutes": max(0, int(popup_reminder_minutes))})
    event = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "reminders": {"useDefault": False, "overrides": overrides[:5]},
    }
    response = requests.post(
        _EVENTS_URL.format(calendar_id=quote(calendar_id, safe="")),
        params={"sendUpdates": "all"},
        headers={"Authorization": f"Bearer {_access_token(user_id)}", "Content-Type": "application/json"},
        json=event,
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Google Calendar event creation failed with HTTP {response.status_code}: {response.text[:300]}")
    return response.json()
