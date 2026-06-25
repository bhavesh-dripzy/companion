"""Send signed Beckn actions to the ONDC gateway (search) or directly to a BPP.

``search`` is broadcast via the gateway; every other action (select/init/confirm/
status/track/cancel/update/rating/support) is directed to a specific BPP's URI.
The body is minified once and that exact byte string is both signed and sent, so
the receiver's recomputed digest matches.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from companion_api.ondc_context import bap_identity, new_context
from companion_api.ondc_crypto import load_ondc_keys
from companion_api.ondc_signing import build_authorization, minify

GATEWAY_BASE = os.getenv("ONDC_GATEWAY_URL", "https://preprod.gateway.ondc.org")
_DIRECTED_TO_BPP = {"select", "init", "confirm", "status", "track", "cancel", "update", "rating", "support"}


def send_action(
    action: str,
    message: dict[str, Any],
    *,
    domain: str,
    transaction_id: str | None = None,
    bpp_id: str | None = None,
    bpp_uri: str | None = None,
    context_overrides: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Build context, sign, and POST a Beckn action. Returns the context sent plus
    the synchronous ACK/NACK from the gateway/BPP (callbacks arrive separately)."""
    keys = load_ondc_keys()
    bap_id, bap_uri = bap_identity(keys)
    context = new_context(
        action,
        domain=domain,
        bap_id=bap_id,
        bap_uri=bap_uri,
        transaction_id=transaction_id,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        **(context_overrides or {}),
    )
    raw = minify({"context": context, "message": message})
    auth = build_authorization(
        raw,
        subscriber_id=keys["subscriber_id"],
        unique_key_id=keys["unique_key_id"],
        signing_private_key_b64=keys["signing_private_key"],
    )

    if action in _DIRECTED_TO_BPP:
        if not bpp_uri:
            raise ValueError(f"{action} must be directed to a bpp_uri")
        target = bpp_uri
    else:
        target = GATEWAY_BASE
    url = f"{target.rstrip('/')}/{action}"

    resp = requests.post(
        url, data=raw, headers={"Content-Type": "application/json", "Authorization": auth}, timeout=timeout
    )
    ack: Any = None
    try:
        ack = resp.json()
    except Exception:
        ack = resp.text
    return {"context": context, "url": url, "status_code": resp.status_code, "ack": ack}


def post_signed(body: dict[str, Any], url: str, timeout: float = 20.0) -> dict[str, Any]:
    """Sign and POST an arbitrary Beckn body (already containing its own context). For
    payloads that aren't the standard {context, message} shape (e.g. catalog_rejection's
    {context, errors})."""
    keys = load_ondc_keys()
    raw = minify(body)
    auth = build_authorization(
        raw,
        subscriber_id=keys["subscriber_id"],
        unique_key_id=keys["unique_key_id"],
        signing_private_key_b64=keys["signing_private_key"],
    )
    resp = requests.post(
        url, data=raw, headers={"Content-Type": "application/json", "Authorization": auth}, timeout=timeout
    )
    try:
        ack = resp.json()
    except Exception:
        ack = resp.text
    return {"url": url, "status_code": resp.status_code, "ack": ack}


def send_catalog_rejection(
    errors: list[dict[str, Any]], *, domain: str, bpp_id: str, bpp_uri: str, transaction_id: str | None = None
) -> dict[str, Any]:
    """Send a ``catalog_rejection`` ({context, errors}) to a BPP (Flow 9)."""
    keys = load_ondc_keys()
    bap_id, bap_uri = bap_identity(keys)
    context = new_context(
        "catalog_rejection", domain=domain, bap_id=bap_id, bap_uri=bap_uri,
        transaction_id=transaction_id, bpp_id=bpp_id, bpp_uri=bpp_uri,
    )
    url = f"{bpp_uri.rstrip('/')}/catalog_rejection"
    result = post_signed({"context": context, "errors": errors}, url)
    return {"context": context, **result}
