"""Twilio voice-call client (ported from `app/services/twilio/executor.py`).

A thin, self-contained wrapper over the Twilio SDK: place a call that bridges to
the Gemini Live voice service, and fetch call status. Credentials come from env
or Secrets Manager. The blocking SDK calls are run off the event loop by callers.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

_VOICE_BRIDGE_URL = os.getenv("TWILIO_VOICE_BRIDGE_URL", "https://voice.houzzat.in")


def _credentials(user_id: str | None) -> tuple[str, str, str]:
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
        raise RuntimeError("Twilio credentials are not configured.")
    return account_sid, auth_token, from_number


def normalize_number(number: str) -> str:
    clean = "".join(c for c in number if c.isdigit() or c == "+")
    if clean.startswith("+") and len(clean) > 2:
        return clean
    if len(clean) == 10 and clean[0] in "6789":
        return f"+91{clean}"
    if 10 <= len(clean) <= 11 and clean[0] == "0":
        return f"+91{clean[1:]}"
    return clean if clean.startswith("+") else f"+{clean}"


def _outbound_url(message: str) -> str:
    return f"{_VOICE_BRIDGE_URL}/outbound-call?message={quote(message, safe='')}"


def make_call(*, to_number: str, message: str, user_id: str | None = None) -> dict[str, Any]:
    try:
        from twilio.rest import Client

        account_sid, auth_token, from_num = _credentials(user_id)
        client = Client(account_sid, auth_token)
        call = client.calls.create(
            to=normalize_number(to_number), from_=from_num, url=_outbound_url(message)
        )
        logger.info("twilio call placed sid=%s status=%s", call.sid, call.status)
        return {"ok": True, "call_sid": call.sid, "status": call.status or "queued", "to": call.to}
    except Exception as exc:
        logger.exception("twilio make_call failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def get_call_status(call_sid: str, user_id: str | None = None) -> dict[str, Any]:
    try:
        from twilio.rest import Client

        account_sid, auth_token, _ = _credentials(user_id)
        client = Client(account_sid, auth_token)
        call = client.calls(call_sid).fetch()
        return {"ok": True, "call_sid": call.sid, "status": call.status}
    except Exception as exc:
        logger.exception("twilio get_call_status failed sid=%s", call_sid)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
