"""Beckn/ONDC request signing + verification (the Authorization header).

Implements ONDC's signing scheme exactly (ref: ONDC reference-implementations
``signing_and_verification``): a BLAKE-512 (blake2b, 64-byte) base64 digest of
the minified JSON body, wrapped in the canonical signing string, then
Ed25519-signed. The signing private key may be raw-32, nacl-64 or PKCS8 — the
loader in ``ondc_crypto`` handles all three. Verification recomputes the digest
over the exact raw body received and checks it against the sender's public key
(fetched via registry lookup).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from companion_api.ondc_crypto import _ed25519_private

_AUTH_TTL_SECONDS = 3600
_CLOCK_SKEW_SECONDS = 300


def minify(body: Any) -> str:
    """Canonical minified JSON (no spaces) — the exact bytes that get hashed/signed."""
    if isinstance(body, (bytes, bytearray)):
        body = json.loads(body)
    if isinstance(body, str):
        body = json.loads(body)
    return json.dumps(body, separators=(",", ":"))


def blake512_b64(message: str) -> str:
    """BLAKE-512 (blake2b, 64-byte) digest of the message, base64-encoded."""
    return base64.b64encode(hashlib.blake2b(message.encode("utf-8"), digest_size=64).digest()).decode()


def signing_string(digest_b64: str, created: int, expires: int) -> str:
    return f"(created): {created}\n(expires): {expires}\ndigest: BLAKE-512={digest_b64}"


def build_authorization(
    body: Any,
    *,
    subscriber_id: str,
    unique_key_id: str,
    signing_private_key_b64: str,
    created: int | None = None,
    expires: int | None = None,
) -> str:
    """Build the Beckn ``Authorization`` header value for an outbound request."""
    created = int(time.time()) if created is None else created
    expires = created + _AUTH_TTL_SECONDS if expires is None else expires
    raw = body if isinstance(body, str) else minify(body)
    message = signing_string(blake512_b64(raw), created, expires)
    sig = base64.b64encode(_ed25519_private(signing_private_key_b64).sign(message.encode("utf-8"))).decode()
    return (
        f'Signature keyId="{subscriber_id}|{unique_key_id}|ed25519",'
        f'algorithm="ed25519",created="{created}",expires="{expires}",'
        f'headers="(created) (expires) digest",signature="{sig}"'
    )


def parse_authorization(header: str) -> dict[str, str]:
    """Parse a Beckn Authorization/Signature header into its key=value parts."""
    return {m.group(1): m.group(2) for m in re.finditer(r'(\w+)="([^"]*)"', header or "")}


def verify_authorization(
    header: str, raw_body: str, signing_public_key_b64: str, *, now: int | None = None
) -> bool:
    """Verify an inbound request's Authorization header against the sender's key.

    ``raw_body`` MUST be the exact request body string as received (not re-parsed),
    so the recomputed digest matches what the sender hashed.
    """
    parts = parse_authorization(header)
    try:
        created, expires, sig = int(parts["created"]), int(parts["expires"]), parts["signature"]
    except (KeyError, ValueError):
        return False
    now = int(time.time()) if now is None else now
    if not (created - _CLOCK_SKEW_SECONDS <= now <= expires + _CLOCK_SKEW_SECONDS):
        return False
    message = signing_string(blake512_b64(raw_body), created, expires)
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(signing_public_key_b64)).verify(
            base64.b64decode(sig), message.encode("utf-8")
        )
        return True
    except Exception:
        return False


def key_id_parts(header: str) -> tuple[str, str]:
    """Extract (subscriber_id, unique_key_id) from a header's keyId for lookup."""
    key_id = parse_authorization(header).get("keyId", "")
    bits = key_id.split("|")
    return (bits[0], bits[1]) if len(bits) >= 2 else ("", "")
