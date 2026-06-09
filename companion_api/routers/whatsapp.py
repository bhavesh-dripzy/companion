"""WhatsApp webhook routes: Meta verification + inbound events.

Extracted verbatim from main.py (#29). The outbound send path is a tool
(core providers/plugins/whatsapp); this is only the inbound webhook.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from companion_api import settings
from companion_api.secrets import secret_or_env
from companion_api.whatsapp_handler import handle_whatsapp_webhook_event

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/whatsapp/webhook")
def whatsapp_webhook_verify(
    request: Request,
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify the WhatsApp webhook with Meta."""
    expected_token = secret_or_env("WHATSAPP_VERIFY_TOKEN", settings.WHATSAPP_VERIFY_TOKEN)
    if mode == "subscribe" and token == expected_token:
        logger.info("WhatsApp webhook verified successfully.")
        return HTMLResponse(content=challenge, status_code=200)
    logger.warning("WhatsApp webhook verification failed. mode=%s, token=%s", mode, token)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/api/whatsapp/webhook")
async def whatsapp_webhook_post(request: Request):
    """Receive incoming WhatsApp messages and events (process directly, return 200)."""
    payload = await request.json()
    try:
        handle_whatsapp_webhook_event(payload)
    except Exception:
        logger.exception("Error handling WhatsApp webhook")
    return {"status": "ok"}
