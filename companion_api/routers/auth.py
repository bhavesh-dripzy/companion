"""Auth routes: login (Cognito or legacy admin), status, cognito config.

Extracted from main.py (#29). The Cognito password-login helper and the config
response model are auth-only and live here.
"""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from companion_api import settings
from companion_api.auth import create_access_token, get_current_user, validate_admin_login
from companion_api.cognito_auth import cognito_enabled

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    refresh_token: str | None = None
    expires_in: int | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int | None = None


def _cognito_client_id() -> str:
    client_id = os.getenv("COGNITO_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito client is not configured.",
        )
    return client_id


def _cognito_password_login(username: str, password: str, remember_me: bool) -> LoginResponse:
    """Authenticate against Cognito; with remember_me the 30-day refresh
    token is returned so devices can renew sessions without re-login."""
    client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)
    try:
        response = client.initiate_auth(
            ClientId=_cognito_client_id(),
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {
            "NotAuthorizedException",
            "UserNotFoundException",
            "PasswordResetRequiredException",
            "UserNotConfirmedException",
        }:
            raise HTTPException(status_code=401, detail="Invalid credentials.") from exc
        raise

    challenge = response.get("ChallengeName")
    if challenge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cognito challenge required: {challenge}.",
        )

    auth = response.get("AuthenticationResult") or {}
    access_token = auth.get("AccessToken")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cognito did not return an access token.",
        )
    return LoginResponse(
        access_token=access_token,
        username=username,
        refresh_token=auth.get("RefreshToken") if remember_me else None,
        expires_in=auth.get("ExpiresIn"),
    )


@router.post("/api/login/")
def login(payload: LoginRequest) -> LoginResponse:
    if cognito_enabled():
        return _cognito_password_login(payload.username, payload.password, payload.remember_me)
    if not validate_admin_login(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    token = create_access_token(payload.username, long_lived=payload.remember_me)
    return LoginResponse(access_token=token, username=payload.username)


@router.post("/api/auth/refresh/")
def refresh(payload: RefreshRequest) -> RefreshResponse:
    """Exchange a Cognito refresh token for a fresh access token. Legacy
    (non-Cognito) mode has no refresh tokens — remember-me logins get a
    long-lived JWT at login instead."""
    if not cognito_enabled():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token refresh is not available in legacy auth mode.",
        )
    client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)
    try:
        response = client.initiate_auth(
            ClientId=_cognito_client_id(),
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": payload.refresh_token},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NotAuthorizedException", "UserNotFoundException"}:
            raise HTTPException(
                status_code=401, detail="Refresh token is invalid or expired."
            ) from exc
        raise

    auth = response.get("AuthenticationResult") or {}
    access_token = auth.get("AccessToken")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cognito did not return an access token.",
        )
    return RefreshResponse(
        access_token=access_token,
        refresh_token=auth.get("RefreshToken"),
        expires_in=auth.get("ExpiresIn"),
    )


@router.get("/api/auth/status/")
def auth_status(user: str = Depends(get_current_user)):
    return {"authenticated": True, "username": user}


class CognitoConfigOut(BaseModel):
    enabled: bool
    region: str = ""
    user_pool_id: str = ""
    client_id: str = ""
    issuer: str = ""
    domain: str = ""


@router.get("/api/auth/cognito/config/", response_model=CognitoConfigOut)
def cognito_config() -> CognitoConfigOut:
    """Return Cognito configuration for client-side SDK setup."""
    from companion_api.cognito_auth import (
        cognito_client_id,
        cognito_issuer,
        cognito_user_pool_id,
    )

    if not cognito_enabled():
        return CognitoConfigOut(enabled=False)
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-south-1"))
    domain = os.getenv("COGNITO_DOMAIN", "")
    return CognitoConfigOut(
        enabled=True,
        region=region,
        user_pool_id=cognito_user_pool_id(),
        client_id=cognito_client_id(),
        issuer=cognito_issuer(),
        domain=domain,
    )
