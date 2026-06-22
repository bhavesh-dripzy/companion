"""ONDC registry lookup — resolve a participant's signing public key.

To verify an inbound callback we need the *sender's* signing public key. The
registry's ``/v2.0/lookup`` returns subscriber records (signing/encryption keys,
validity) filtered by subscriber_id + ukId. Results are cached briefly since keys
rotate rarely and callbacks arrive in bursts.
"""

from __future__ import annotations

import os
import time

import requests

REGISTRY_BASE = os.getenv("ONDC_REGISTRY_BASE", "https://preprod.registry.ondc.org")
_LOOKUP_TTL = 600
_cache: dict[tuple[str, str], tuple[float, str]] = {}


def lookup_signing_key(subscriber_id: str, unique_key_id: str, *, timeout: float = 15.0) -> str:
    """Return the base64 signing_public_key for a subscriber+ukId, or '' if not found."""
    if not subscriber_id:
        return ""
    cache_key = (subscriber_id, unique_key_id)
    hit = _cache.get(cache_key)
    if hit and (time.time() - hit[0]) < _LOOKUP_TTL:
        return hit[1]

    payload: dict[str, str] = {"subscriber_id": subscriber_id}
    if unique_key_id:
        payload["ukId"] = unique_key_id
    try:
        resp = requests.post(f"{REGISTRY_BASE}/v2.0/lookup", json=payload, timeout=timeout)
        resp.raise_for_status()
        records = resp.json()
    except Exception:
        return ""

    records = records if isinstance(records, list) else [records]
    for rec in records:
        if unique_key_id and rec.get("ukId") not in (unique_key_id, None):
            continue
        key = rec.get("signing_public_key", "")
        if key:
            _cache[cache_key] = (time.time(), key)
            return key
    return ""
