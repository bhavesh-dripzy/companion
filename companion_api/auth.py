"""JWT-based authentication for Companion API.

Admin credentials are multi-account by default.

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

DEFAULT_ADMINS = {
    "admin_aashish": "admin_aashish445*",
    "admin_bhavesh": "admin_bhavesh445*",
    "admin_kshitij": "admin_kshitij445*",
}


@lru_cache(maxsize=1)
def _admin_credentials() -> dict[str, str]:
    return dict(DEFAULT_ADMINS)


def _jwt_secret() -> str:
    """Derive a stable signing key from the admin password + optional env override."""
    explicit = secret_or_env("JWT_SECRET").strip()
    if explicit:
        return explicit
    seed = "|".join(f"{u}:{p}" for u, p in sorted(_admin_credentials().items()))
    return hashlib.sha256(f"companion:{seed}".encode()).hexdigest()


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
        username = payload.get("sub")
        return username if username in _admin_credentials() else None
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
    return _admin_credentials().get(username) == password
