"""Auth routes: login (Cognito or legacy admin), OTP sign-in, status, config.

Extracted from main.py (#29). The Cognito password-login helper and the config
response model are auth-only and live here.

OTP sign-in (passwordless): /api/auth/otp/start/ initiates Cognito's
USER_AUTH flow with an SMS_OTP challenge (lazily provisioning the user on
first login, keyed by phone number), /api/auth/otp/verify/ answers the
challenge and returns tokens. Username/password sign-in stays as a fallback.
"""

from __future__ import annotations

import os
import re
import secrets
import string

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


# ---------------------------------------------------------------------------
# Passwordless SMS-OTP sign-in (Cognito USER_AUTH flow, SMS_OTP factor)
# ---------------------------------------------------------------------------
# The user is identified by their phone number. To avoid ever mutating the
# live pool's (immutable) sign-in aliases, we DON'T make phone a Cognito alias;
# instead the Cognito username is the E.164 digits (e.g. "919876543210") — a
# deterministic, stable id — and the real number lives in the `phone_number`
# attribute, which is where Cognito delivers the SMS_OTP code.


class OtpStartRequest(BaseModel):
    phone: str


class OtpStartResponse(BaseModel):
    session: str
    username: str  # the Cognito username the client echoes back on verify
    destination: str = ""  # masked number Cognito texted, for display


class OtpVerifyRequest(BaseModel):
    username: str
    session: str
    code: str
    remember_me: bool = False


def _cognito():
    return boto3.client("cognito-idp", region_name=settings.AWS_REGION)


def _cognito_pool_id() -> str:
    pool_id = os.getenv("COGNITO_USER_POOL_ID", "").strip()
    if not pool_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito is not configured.",
        )
    return pool_id


def _normalize_phone(raw: str) -> str | None:
    """Coerce user input to E.164. A bare 10-digit number is assumed Indian
    (+91); anything already carrying a country code (leading + or 11-15 digits)
    is kept. Returns None if it can't be a valid E.164 number."""
    digits = re.sub(r"\D", "", raw or "")
    if raw.strip().startswith("+"):
        e164 = "+" + digits
    elif len(digits) == 10:  # bare Indian mobile
        e164 = "+91" + digits
    elif digits.startswith("91") and len(digits) == 12:
        e164 = "+" + digits
    else:
        e164 = "+" + digits
    return e164 if re.fullmatch(r"\+[1-9]\d{7,14}", e164) else None


def _initiate_sms_otp(client, username: str) -> dict:
    """Start USER_AUTH preferring the SMS_OTP challenge for this user."""
    response = client.initiate_auth(
        ClientId=_cognito_client_id(),
        AuthFlow="USER_AUTH",
        AuthParameters={"USERNAME": username, "PREFERRED_CHALLENGE": "SMS_OTP"},
    )
    # If the pool made us pick a factor first, answer with SMS_OTP.
    if response.get("ChallengeName") == "SELECT_CHALLENGE":
        response = client.respond_to_auth_challenge(
            ClientId=_cognito_client_id(),
            ChallengeName="SELECT_CHALLENGE",
            Session=response["Session"],
            ChallengeResponses={"USERNAME": username, "ANSWER": "SMS_OTP"},
        )
    return response


def _provision_otp_user(client, username: str, phone_e164: str) -> None:
    """First-time OTP login: create the user with a verified phone number and a
    random permanent password (never shared — sign-in stays passwordless, and
    CONFIRMED status is required for the SMS_OTP factor)."""
    try:
        client.admin_create_user(
            UserPoolId=_cognito_pool_id(),
            Username=username,
            UserAttributes=[
                {"Name": "phone_number", "Value": phone_e164},
                {"Name": "phone_number_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
    except client.exceptions.UsernameExistsException:
        return  # already provisioned on an earlier login — nothing to do
    alphabet = string.ascii_lowercase + string.ascii_uppercase + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(24)) + "1aA"
    client.admin_set_user_password(
        UserPoolId=_cognito_pool_id(),
        Username=username,
        Password=password,
        Permanent=True,
    )


@router.post("/api/auth/otp/start/")
def otp_start(payload: OtpStartRequest) -> OtpStartResponse:
    """Send a one-time sign-in code by SMS to the given phone number (creating
    the account on first login), and return the challenge session."""
    if not cognito_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OTP sign-in requires Cognito, which is not configured.",
        )
    phone = _normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=422, detail="Enter a valid mobile number.")
    username = phone.lstrip("+")  # deterministic, stable Cognito username

    client = _cognito()
    try:
        response = _initiate_sms_otp(client, username)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # With prevent-user-existence-errors the "no such user" case surfaces
        # as NotAuthorized — provision the account and retry once.
        if code in {"UserNotFoundException", "NotAuthorizedException"}:
            try:
                _provision_otp_user(client, username, phone)
                response = _initiate_sms_otp(client, username)
            except ClientError as exc2:
                raise HTTPException(
                    status_code=401, detail="Could not start OTP sign-in for this number."
                ) from exc2
        else:
            raise

    if response.get("ChallengeName") != "SMS_OTP" or not response.get("Session"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cognito did not offer an SMS OTP challenge.",
        )
    params = response.get("ChallengeParameters") or {}
    return OtpStartResponse(
        session=response["Session"],
        username=username,
        destination=params.get("CODE_DELIVERY_DESTINATION", ""),
    )


@router.post("/api/auth/otp/verify/")
def otp_verify(payload: OtpVerifyRequest) -> LoginResponse:
    """Answer the SMS_OTP challenge with the code the user received."""
    if not cognito_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OTP sign-in requires Cognito, which is not configured.",
        )
    code = payload.code.strip()
    if not re.fullmatch(r"\d{4,8}", code):
        raise HTTPException(status_code=422, detail="Enter the numeric code from the SMS.")

    client = _cognito()
    try:
        response = client.respond_to_auth_challenge(
            ClientId=_cognito_client_id(),
            ChallengeName="SMS_OTP",
            Session=payload.session,
            ChallengeResponses={
                "USERNAME": re.sub(r"\D", "", payload.username),
                "SMS_OTP_CODE": code,
            },
        )
    except ClientError as exc:
        error = exc.response.get("Error", {}).get("Code", "")
        if error in {"CodeMismatchException", "NotAuthorizedException"}:
            raise HTTPException(
                status_code=401, detail="That code is incorrect. Please try again."
            ) from exc
        if error in {"ExpiredCodeException", "ExpiredSessionException"}:
            raise HTTPException(
                status_code=401, detail="The code expired. Request a new one."
            ) from exc
        raise

    auth = response.get("AuthenticationResult") or {}
    access_token = auth.get("AccessToken")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cognito did not return an access token.",
        )
    return LoginResponse(
        access_token=access_token,
        username=re.sub(r"\D", "", payload.username),
        refresh_token=auth.get("RefreshToken") if payload.remember_me else None,
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
