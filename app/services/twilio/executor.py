"""Twilio voice call executor — Gemini Live voice via EC2 bridge."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

_VOICE_BRIDGE_URL = os.getenv(
    "TWILIO_VOICE_BRIDGE_URL", "https://voice.houzzat.in"
)


def make_call(
    *,
    to_number: str,
    message: str,
    from_number: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Initiate a Twilio call that connects to Gemini Live voice.

    Uses ``<Connect><Stream>`` to bridge the call audio to the EC2 voice
    service, which streams bidirectional audio to/from Gemini Live API.
    The person hears Gemini's natural conversational voice and can talk back.

    Args:
        to_number: Destination phone number (normalized to E.164).
        message: The message Gemini should deliver to the person.
        from_number: Optional Twilio "from" number (falls back to env).
        user_id: Optional user identifier for credential isolation.

    Returns:
        dict with ``ok``, ``call_sid``, ``status`` on success.
    """
    try:
        from twilio.rest import Client

        account_sid, auth_token, from_num = _get_twilio_credentials(user_id)
        client = Client(account_sid, auth_token)

        normalized_to = _normalize_number(to_number)
        outbound_url = _build_outbound_url(message)
        call = client.calls.create(
            to=normalized_to,
            from_=from_number or from_num,
            url=outbound_url,
        )
        logger.info(
            "twilio call placed — sid=%s to=%s status=%s bridge=%s",
            call.sid,
            normalized_to,
            call.status,
            _VOICE_BRIDGE_URL,
        )
        return {
            "ok": True,
            "call_sid": call.sid,
            "status": call.status or "queued",
            "to": call.to,
            "from": call._from,
        }
    except Exception as exc:
        logger.exception("twilio make_call failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def get_call_status(
    call_sid: str, user_id: str | None = None
) -> dict[str, Any]:
    """Fetch the current status of a Twilio call."""
    try:
        from twilio.rest import Client

        account_sid, auth_token, _ = _get_twilio_credentials(user_id)
        client = Client(account_sid, auth_token)
        call = client.calls(call_sid).fetch()
        return {"ok": True, "call_sid": call.sid, "status": call.status}
    except Exception as exc:
        logger.exception(
            "twilio get_call_status failed for sid=%s", call_sid
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _build_outbound_url(message: str) -> str:
    """Build the outbound-call URL with the message as query param.

    Twilio fetches this URL to get TwiML instructions when the call connects.
    The EC2 voice service returns <Connect><Stream> TwiML pointing to its
    WebSocket endpoint with the message embedded.
    """
    encoded = quote(message, safe="")
    return f"{_VOICE_BRIDGE_URL}/outbound-call?message={encoded}"


def _get_twilio_credentials(
    user_id: str | None,
) -> tuple[str, str, str]:
    """Resolve Twilio credentials.

    Priority: environment variables → AWS Secrets Manager.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()

    if not all([account_sid, auth_token, from_number]):
        try:
            from companion_api.secrets import secret_or_env

            account_sid = account_sid or secret_or_env("TWILIO_ACCOUNT_SID")
            auth_token = auth_token or secret_or_env("TWILIO_AUTH_TOKEN")
            from_number = from_number or secret_or_env("TWILIO_FROM_NUMBER")
        except Exception:
            pass

    if not all([account_sid, auth_token, from_number]):
        raise RuntimeError(
            "Twilio credentials are not configured. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER in the environment or "
            "AWS Secrets Manager."
        )
    return account_sid, auth_token, from_number


def _normalize_number(number: str) -> str:
    """Normalize a phone number to E.164 format."""
    clean = "".join(c for c in number if c.isdigit() or c == "+")
    if clean.startswith("+") and len(clean) > 2:
        return clean
    if len(clean) == 10 and clean[0] in "6789":
        return f"+91{clean}"
    if 10 <= len(clean) <= 11 and clean[0] == "0":
        return f"+91{clean[1:]}"
    if not clean.startswith("+"):
        clean = f"+{clean}"
    return clean
