"""Authentication for Companion API.

Primary auth: AWS Cognito User Pool (JWT verified via JWKS).
Legacy fallback: env-var-based admin credentials + self-issued JWT.

When COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are set in the
environment, Cognito tokens take priority.  The legacy mode uses
LEGACY_ADMIN_USERNAME / LEGACY_ADMIN_PASSWORD from the environment
and is intended only for local development.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from companion_api.cognito_auth import (
    CognitoTokenError,
    cognito_enabled,
    verify_cognito_token,
)
from companion_api.secrets import secret_or_env

# ---------------------------------------------------------------------------
# Legacy admin (env vars only — no hardcoded credentials)
# ---------------------------------------------------------------------------

_LEGACY_ADMIN_USERNAME = os.getenv("LEGACY_ADMIN_USERNAME", "").strip()
_LEGACY_ADMIN_PASSWORD = os.getenv("LEGACY_ADMIN_PASSWORD", "").strip()


def _legacy_admin_enabled() -> bool:
    return bool(_LEGACY_ADMIN_USERNAME and _LEGACY_ADMIN_PASSWORD)


def _jwt_secret() -> str:
    """Derive a stable signing key for legacy JWT tokens."""
    explicit = secret_or_env("JWT_SECRET").strip()
    if explicit:
        return explicit
    if _legacy_admin_enabled():
        return hashlib.sha256(
            f"companion:{_LEGACY_ADMIN_USERNAME}:{_LEGACY_ADMIN_PASSWORD}".encode()
        ).hexdigest()
    # No JWT_SECRET and no legacy admin — generate an ephemeral key.
    # This means tokens won't survive restarts; Cognito is required for
    # production.
    return hashlib.sha256(os.urandom(32)).hexdigest()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

ACCESS_TOKEN_EXPIRE_HOURS = 24


def create_access_token(username: str) -> str:
    """Issue a legacy self-signed JWT (used only when Cognito is unavailable)."""
    payload = {
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_access_token(token: str) -> str | None:
    """Return username if legacy JWT is valid, None otherwise.

    The legacy path accepts any well-formed token signed with the
    current JWT secret — there is no longer a hardcoded admin list.
    """
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        username = payload.get("sub")
        return username if isinstance(username, str) and username else None
    except jwt.PyJWTError:
        return None


def validate_admin_login(username: str, password: str) -> bool:
    """Validate legacy admin credentials (env-var based)."""
    if not _legacy_admin_enabled():
        return False
    return username == _LEGACY_ADMIN_USERNAME and password == _LEGACY_ADMIN_PASSWORD


# ---------------------------------------------------------------------------
# FastAPI dependency (unified)
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> str:
    """Extract and validate the authenticated user from the Authorization header.

    Priority:
      1. Cognito JWT (Access or ID token) — if COGNITO_USER_POOL_ID is set.
      2. Legacy self-signed JWT — for local dev and backward compat.

    Raises HTTPException 401 on any failure.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header.",
        )

    token = credentials.credentials

    # ── Try Cognito first ──────────────────────────────────────────────
    if cognito_enabled():
        try:
            claims = verify_cognito_token(token)
            # Prefer 'username' from ID token, fall back to 'sub'
            username = claims.get("username") or claims.get("sub")
            if username:
                return username
        except CognitoTokenError:
            pass  # not a valid Cognito token — fall through to legacy

    # ── Fall back to legacy JWT ────────────────────────────────────────
    username = verify_access_token(token)
    if username is not None:
        return username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
    )
