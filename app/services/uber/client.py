from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import boto3
import requests

from companion_api import settings
from companion_api.secrets import secret_or_env

_TOKEN_SECRET_ID = "companion/uber-tokens"
_AUTH_URL = "https://login.uber.com/oauth/v2/authorize"
_TOKEN_URL = "https://login.uber.com/oauth/v2/token"
_PROD_API_BASE = "https://api.uber.com/v1.2"
_SANDBOX_API_BASE = "https://sandbox-api.uber.com/v1.2"
_PENDING_TTL_SECONDS = 15 * 60
_SCOPES = ("profile", "request", "all_trips", "offline_access")


class UberError(RuntimeError):
    def __init__(self, message: str, *, code: str = "uber_error") -> None:
        super().__init__(message)
        self.code = code


def _client() -> Any:
    return boto3.client("secretsmanager", region_name=settings.AWS_REGION)


def _secret_id() -> str:
    return secret_or_env("UBER_TOKEN_SECRET_ID", _TOKEN_SECRET_ID)


def _local_token_store_path() -> Path | None:
    configured = os.getenv("UBER_TOKEN_STORE_FILE", "").strip()
    return Path(configured).expanduser() if configured else None


def _root_secret() -> dict[str, Any]:
    local_path = _local_token_store_path()
    if local_path:
        try:
            parsed = json.loads(local_path.read_text())
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    try:
        response = _client().get_secret_value(SecretId=_secret_id())
        parsed = json.loads(response.get("SecretString") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _write_root_secret(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":"))
    local_path = _local_token_store_path()
    if local_path:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(raw)
        return

    client = _client()
    try:
        client.put_secret_value(SecretId=_secret_id(), SecretString=raw)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(
            Name=_secret_id(),
            SecretString=raw,
            Description="Uber OAuth tokens for Companion users",
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
    client_id = secret_or_env("UBER_CLIENT_ID")
    client_secret = secret_or_env("UBER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise UberError("Uber OAuth client is not configured.", code="not_configured")
    return client_id, client_secret


def uber_configured() -> bool:
    return bool(secret_or_env("UBER_CLIENT_ID") and secret_or_env("UBER_CLIENT_SECRET"))


def uber_environment() -> str:
    env = secret_or_env("UBER_ENVIRONMENT", "sandbox").strip().lower()
    return "production" if env in {"prod", "production"} else "sandbox"


def _api_base() -> str:
    configured = secret_or_env("UBER_API_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return _PROD_API_BASE if uber_environment() == "production" else _SANDBOX_API_BASE


def start_uber_oauth(*, user_id: str, redirect_uri: str) -> dict[str, str]:
    client_id, _client_secret = _credentials()
    scopes = tuple(
        scope.strip()
        for scope in secret_or_env("UBER_OAUTH_SCOPES", " ".join(_SCOPES)).split()
        if scope.strip()
    ) or _SCOPES
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
        "scope": " ".join(scopes),
        "state": state,
    }
    return {"authorization_url": f"{_AUTH_URL}?{urlencode(params)}", "state": state}


def complete_uber_oauth(*, code: str, state: str) -> dict[str, Any]:
    client_id, client_secret = _credentials()
    payload = _root_secret()
    pending = payload.get("oauth_pending")
    if not isinstance(pending, dict) or state not in pending:
        raise UberError("Uber OAuth state was not recognized. Start connection again.", code="invalid_state")
    session = pending.pop(state)
    if int(time.time()) - int(session.get("created_at", 0)) > _PENDING_TTL_SECONDS:
        payload["oauth_pending"] = pending
        _write_root_secret(payload)
        raise UberError("Uber OAuth session expired. Start connection again.", code="invalid_state")

    response = requests.post(
        _TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": str(session["redirect_uri"]),
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if not response.ok:
        raise UberError(f"Uber token exchange failed with HTTP {response.status_code}: {response.text[:300]}")
    tokens = response.json()
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise UberError("Uber token exchange did not return an access token.")
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


def uber_status(user_id: str) -> dict[str, Any]:
    tokens = ((_root_secret().get("users") or {}).get(user_id) or {}).get("tokens")
    connected = isinstance(tokens, dict) and bool(tokens.get("refresh_token") or tokens.get("access_token"))
    scopes = []
    if isinstance(tokens, dict):
        raw_scope = tokens.get("scope") or tokens.get("scopes")
        if isinstance(raw_scope, str):
            scopes = raw_scope.split()
    return {
        "configured": uber_configured(),
        "connected": connected,
        "environment": uber_environment(),
        "scopes": scopes,
        "can_request_rides": "request" in scopes or not scopes,
        "production_full_access_required": uber_environment() == "production",
    }


def _tokens_for_user(user_id: str) -> dict[str, Any]:
    tokens = ((_root_secret().get("users") or {}).get(user_id) or {}).get("tokens")
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise UberError("Uber is not connected for this user.", code="not_connected")
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
        raise UberError(f"Uber token refresh failed with HTTP {response.status_code}: {response.text[:300]}")
    refreshed = response.json()
    tokens.update(refreshed)
    if refreshed.get("expires_in"):
        tokens["expires_at"] = int(time.time()) + int(refreshed["expires_in"])

    payload = _root_secret()
    _user_block(payload, user_id)["tokens"] = tokens
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_root_secret(payload)
    return str(tokens["access_token"])


def _headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(user_id)}", "Content-Type": "application/json"}


def _request(method: str, path: str, *, user_id: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(
        method,
        f"{_api_base()}{path}",
        headers=_headers(user_id),
        timeout=20,
        **kwargs,
    )
    if not response.ok:
        code = "upstream_error"
        try:
            payload = response.json()
            code = str(payload.get("code") or payload.get("error") or code)
        except Exception:
            payload = response.text[:300]
        raise UberError(f"Uber API HTTP {response.status_code}: {payload}", code=code)
    if response.status_code == 204 or not response.text:
        return {"ok": True}
    payload = response.json()
    return payload if isinstance(payload, dict) else {"data": payload}


def get_products(*, user_id: str, latitude: float, longitude: float) -> dict[str, Any]:
    return _request(
        "GET",
        "/products",
        user_id=user_id,
        params={"latitude": latitude, "longitude": longitude},
    )


def estimate_ride(
    *,
    user_id: str,
    product_id: str,
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    seat_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_id": product_id,
        "start_latitude": start_latitude,
        "start_longitude": start_longitude,
        "end_latitude": end_latitude,
        "end_longitude": end_longitude,
    }
    if seat_count:
        payload["seat_count"] = seat_count
    return _request("POST", "/requests/estimate", user_id=user_id, json=payload)


def create_ride(
    *,
    user_id: str,
    product_id: str,
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    fare_id: str | None = None,
    seat_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_id": product_id,
        "start_latitude": start_latitude,
        "start_longitude": start_longitude,
        "end_latitude": end_latitude,
        "end_longitude": end_longitude,
    }
    if fare_id:
        payload["fare_id"] = fare_id
    if seat_count:
        payload["seat_count"] = seat_count
    return _request("POST", "/requests", user_id=user_id, json=payload)


def get_ride_status(*, user_id: str, request_id: str) -> dict[str, Any]:
    return _request("GET", f"/requests/{request_id}", user_id=user_id)


def cancel_ride(*, user_id: str, request_id: str) -> dict[str, Any]:
    return _request("DELETE", f"/requests/{request_id}", user_id=user_id)
