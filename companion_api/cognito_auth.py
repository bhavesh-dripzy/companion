"""Cognito JWT verification via JWKS.

Verifies Cognito-issued Access and ID tokens against the User Pool's
public JWKS endpoint.  Tokens are validated for:

* correct issuer (User Pool)
* correct audience (Client ID)
* token_use claim (access or id)
* expiry and not-before
* cryptographic signature (RS256 via JWKS)

The JWKS response is cached in memory for the standard key-rotation
window (1 hour default).
"""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import Any

import jwt
import requests
from jwt import PyJWKClient, PyJWKClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

_COGNITO_REGION = (
    os.getenv("AWS_REGION")
    or os.getenv("AWS_DEFAULT_REGION")
    or "ap-south-1"
)
_COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "").strip()
_COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "").strip()

_COGNITO_ISSUER = (
    f"https://cognito-idp.{_COGNITO_REGION}.amazonaws.com/{_COGNITO_USER_POOL_ID}"
    if _COGNITO_USER_POOL_ID
    else ""
)

_JWKS_URL = (
    f"{_COGNITO_ISSUER}/.well-known/jwks.json" if _COGNITO_ISSUER else ""
)

# JWKS cache TTL — Cognito key rotation is infrequent (days/weeks).
# 1-hour in-memory cache keeps latency low while picking up rotations
# reasonably fast.
_JWKS_CACHE_TTL_SECONDS = 3600

# ---------------------------------------------------------------------------
# JWKS client (cached)
# ---------------------------------------------------------------------------

_jwks_client: PyJWKClient | None = None
_jwks_client_ts: float = 0.0


def _get_jwks_client() -> PyJWKClient | None:
    """Return a cached PyJWKClient, refreshing after TTL."""
    global _jwks_client, _jwks_client_ts
    if not _JWKS_URL:
        return None
    now = time.time()
    if _jwks_client is None or (now - _jwks_client_ts) > _JWKS_CACHE_TTL_SECONDS:
        _jwks_client = PyJWKClient(_JWKS_URL, cache_keys=True)
        _jwks_client_ts = now
    return _jwks_client


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


class CognitoTokenError(Exception):
    """Token validation failed."""


def verify_cognito_token(token: str) -> dict[str, Any]:
    """Verify a Cognito-issued JWT and return its claims.

    Accepts both Access and ID tokens.  The token_use claim is
    validated: ``access`` tokens must have ``client_id`` as audience;
    ``id`` tokens must have ``aud`` matching the client ID.

    Raises ``CognitoTokenError`` for any validation failure.
    """
    if not _COGNITO_USER_POOL_ID or not _COGNITO_CLIENT_ID:
        raise CognitoTokenError(
            "Cognito is not configured — set COGNITO_USER_POOL_ID "
            "and COGNITO_CLIENT_ID."
        )

    if not token or not isinstance(token, str):
        raise CognitoTokenError("Token is empty or not a string.")

    jwks = _get_jwks_client()
    if jwks is None:
        raise CognitoTokenError("JWKS endpoint URL could not be constructed.")

    # 1. Decode header to get kid, then fetch the matching key
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise CognitoTokenError(f"Invalid JWT header: {e}") from e

    kid = unverified_header.get("kid")
    if not kid:
        raise CognitoTokenError("Token header missing 'kid' claim.")

    try:
        signing_key = jwks.get_signing_key(kid)
        if signing_key is None:
            raise CognitoTokenError(f"JWK with kid={kid!r} not found in JWKS.")
    except PyJWKClientError as e:
        raise CognitoTokenError(f"Failed to fetch JWKS: {e}") from e

    # 2. Decode + validate
    try:
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            issuer=_COGNITO_ISSUER,
            options={
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": False,  # we validate manually below
            },
        )
    except jwt.ExpiredSignatureError:
        raise CognitoTokenError("Token has expired.")
    except jwt.InvalidIssuerError:
        raise CognitoTokenError(
            f"Invalid issuer — expected {_COGNITO_ISSUER}."
        )
    except jwt.PyJWTError as e:
        raise CognitoTokenError(f"Token validation failed: {e}") from e

    # 3. token_use + audience
    token_use = claims.get("token_use", "")
    if token_use == "access":
        client_id = claims.get("client_id", "")
        if client_id != _COGNITO_CLIENT_ID:
            raise CognitoTokenError("Access token client_id mismatch.")
    elif token_use == "id":
        aud = claims.get("aud", "")
        if aud != _COGNITO_CLIENT_ID and _COGNITO_CLIENT_ID not in (
            [aud] if isinstance(aud, str) else list(aud)
        ):
            raise CognitoTokenError("ID token audience mismatch.")
    else:
        raise CognitoTokenError(
            f"Unexpected token_use: {token_use!r}. Expected 'access' or 'id'."
        )

    return claims


def cognito_enabled() -> bool:
    """Return True if Cognito is configured via environment variables."""
    return bool(_COGNITO_USER_POOL_ID and _COGNITO_CLIENT_ID)


def cognito_user_pool_id() -> str:
    return _COGNITO_USER_POOL_ID


def cognito_client_id() -> str:
    return _COGNITO_CLIENT_ID


def cognito_issuer() -> str:
    return _COGNITO_ISSUER
