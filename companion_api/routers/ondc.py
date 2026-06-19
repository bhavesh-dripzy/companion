"""ONDC onboarding endpoints: registry site-verification + /on_subscribe handshake.

These are the two routes the ONDC registry probes when we call /subscribe (see
scripts/ondc_subscribe.py): it fetches /ondc-site-verification.html (served at
the domain root) to verify the signed request_id, and POSTs an encrypted
challenge to <callback_url>/on_subscribe which we decrypt and echo back. The
transaction callbacks (/on_search, /on_select, ...) are added later, once the
subscriber_id is live on the test network.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from companion_api.ondc_crypto import decrypt_challenge, load_ondc_keys, sign_request_id

logger = logging.getLogger(__name__)

router = APIRouter()

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
