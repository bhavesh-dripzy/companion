"""ONDC endpoints: registry onboarding handshake + Beckn transaction callbacks.

Onboarding: /ondc-site-verification.html (signed request_id) and /ondc/on_subscribe
(challenge decrypt) — what the registry probes during /subscribe.

Transactions (BAP): the async on_* callbacks (on_search ... on_support) the
network sends back after we dispatch an action. Each is signature-verified
against the sender's registry key, persisted, and ACK'd per the Beckn contract.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from companion_api.ondc_crypto import decrypt_challenge, load_ondc_keys, sign_request_id
from companion_api.ondc_flow import advance, start_order_flow
from companion_api.ondc_registry import lookup_signing_key
from companion_api.ondc_signing import key_id_parts, verify_authorization
from companion_api.ondc_store import record_callback

logger = logging.getLogger(__name__)

router = APIRouter()

_CALLBACK_ACTIONS = {
    "on_search",
    "on_select",
    "on_init",
    "on_confirm",
    "on_status",
    "on_track",
    "on_cancel",
    "on_update",
    "on_rating",
    "on_support",
}
_STRICT_CALLBACKS = os.getenv("ONDC_STRICT_CALLBACKS", "false").lower() == "true"
# Shared secret gating the test-flow trigger (unset => trigger disabled).
_FLOW_TOKEN = os.getenv("ONDC_FLOW_TOKEN", "")


def _ack() -> dict[str, Any]:
    return {"message": {"ack": {"status": "ACK"}}}


def _nack(code: str, message: str) -> dict[str, Any]:
    return {
        "message": {"ack": {"status": "NACK"}},
        "error": {"type": "CONTEXT-ERROR", "code": code, "message": message},
    }

_SITE_VERIFICATION_HTML = """<html>
    <head>
        <meta name="ondc-site-verification" content="{signature}" />
    </head>
    <body>
        ONDC Site Verification Page
    </body>
</html>"""


@router.get("/ondc-site-verification.html", response_class=HTMLResponse)
def ondc_site_verification(request_id: str = Query(default="")) -> HTMLResponse:
    """Serve the registry's site-verification page (Ed25519-signed request_id).

    Signs the request_id from the key bundle; an explicit ``?request_id=`` query
    overrides it so the page can be matched to whatever value the registry uses.
    """
    keys = load_ondc_keys()
    req = request_id.strip() or keys.get("request_id", "")
    signing_key = keys.get("signing_private_key", "")
    if not req or not signing_key:
        raise HTTPException(status_code=503, detail="ONDC keys not configured")
    return HTMLResponse(_SITE_VERIFICATION_HTML.format(signature=sign_request_id(req, signing_key)))


@router.post("/ondc/on_subscribe")
async def ondc_on_subscribe(payload: dict[str, Any]) -> dict[str, str]:
    """Answer the registry's encrypted subscribe challenge."""
    challenge = (payload or {}).get("challenge", "")
    if not challenge:
        raise HTTPException(status_code=400, detail="missing challenge")
    enc_key = load_ondc_keys().get("encryption_private_key", "")
    if not enc_key:
        raise HTTPException(status_code=503, detail="ONDC keys not configured")
    try:
        return {"answer": decrypt_challenge(challenge, enc_key)}
    except Exception:
        logger.exception("ONDC on_subscribe challenge decryption failed")
        raise HTTPException(status_code=500, detail="challenge decryption failed")


def _verify_callback(auth_header: str, raw_body: str) -> bool:
    """Best-effort signature check: resolve the sender's key via registry, verify."""
    if not auth_header:
        return False
    subscriber_id, unique_key_id = key_id_parts(auth_header)
    signing_key = lookup_signing_key(subscriber_id, unique_key_id)
    if not signing_key:
        logger.warning("ONDC callback: no signing key for %s|%s; cannot verify", subscriber_id, unique_key_id)
        return False
    return verify_authorization(auth_header, raw_body, signing_key)


@router.post("/ondc/flow/start")
async def ondc_flow_start(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Trigger a BAP transaction flow (for Pramaan). Gated by the X-Flow-Token header."""
    if not _FLOW_TOKEN or request.headers.get("X-Flow-Token", "") != _FLOW_TOKEN:
        raise HTTPException(status_code=403, detail="flow trigger disabled")
    query = (payload or {}).get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    return start_order_flow(
        query,
        gps=payload.get("gps"),
        area_code=payload.get("area_code"),
        domain=payload.get("domain", "ONDC:RET10"),
        category_id=payload.get("category_id"),
    )


@router.post("/ondc/{action}")
async def ondc_callback(action: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Receive an async Beckn callback (on_search ... on_support).

    Declared after /ondc/on_subscribe so that route wins; this catches the rest.
    Verifies the sender's signature, persists the body, ACK/NACKs, and (for ``on_*``
    callbacks) advances the transaction flow to the next action in the background.
    """
    if action not in _CALLBACK_ACTIONS:
        raise HTTPException(status_code=404, detail="unknown ONDC action")
    raw_bytes = await request.body()
    if raw_bytes[:2] == b"\x1f\x8b":
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except OSError:
            return _nack("10001", "invalid gzip body")
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return _nack("10001", "invalid body encoding")
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return _nack("10001", "invalid JSON body")

    verified = _verify_callback(request.headers.get("Authorization", ""), raw)
    if _STRICT_CALLBACKS and not verified:
        return _nack("20001", "signature verification failed")
    record_callback(action, body, verified=verified)
    if action.startswith("on_"):
        background_tasks.add_task(advance, action, body)
    return _ack()
