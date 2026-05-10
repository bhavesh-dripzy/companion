"""JWT-based authentication for Companion API.

Admin credentials are resolved via the same secret_or_env chain as LLM keys:
  1. ADMIN_USERNAME / ADMIN_PASSWORD env vars
  2. companion/app secret in AWS Secrets Manager
  3. Hard fallback defaults (admin / admin112###)

A JWT_SECRET is derived from the admin password by default.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from companion_api.secrets import secret_or_env

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin112###"


@lru_cache(maxsize=1)
def _admin_credentials() -> tuple[str, str]:
    username = (
        secret_or_env("ADMIN_USERNAME").strip() or DEFAULT_ADMIN_USERNAME
    )
    password = (
        secret_or_env("ADMIN_PASSWORD").strip() or DEFAULT_ADMIN_PASSWORD
    )
    return username, password


def _jwt_secret() -> str:
    """Derive a stable signing key from the admin password + optional env override."""
    explicit = secret_or_env("JWT_SECRET").strip()
    if explicit:
        return explicit
    _, password = _admin_credentials()
    return hashlib.sha256(f"companion:{password}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

ACCESS_TOKEN_EXPIRE_HOURS = 24


def create_access_token(username: str) -> str:
    payload = {
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_access_token(token: str) -> str | None:
    """Return username if valid, None otherwise."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header.",
        )
    username = verify_access_token(credentials.credentials)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )
    return username


def validate_admin_login(username: str, password: str) -> bool:
    expected_user, expected_pass = _admin_credentials()
    return username == expected_user and password == expected_pass
