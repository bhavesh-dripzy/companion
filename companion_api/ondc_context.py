"""Beckn ``context`` builder for ONDC BAP requests.

Every ONDC request/callback carries a ``context`` identifying the domain, action,
the two participants (bap/bpp), and the transaction/message correlation ids. This
builds a spec-shaped context; ``bap_identity`` derives our ids from the key bundle.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from companion_api.ondc_crypto import load_ondc_keys

# RET10 (B2C grocery) certifies on Beckn 1.2.0, whose context field is
# ``core_version`` (2.x uses ``version`` and is B2B). Override via env if needed.
DEFAULT_CORE_VERSION = os.getenv("ONDC_CORE_VERSION", "1.2.0")


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def bap_identity(keys: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (bap_id, bap_uri) — our subscriber id and callback base URL."""
    keys = keys or load_ondc_keys()
    subscriber_id = keys.get("subscriber_id", "")
    bap_uri = os.getenv("ONDC_BAP_URI", "").strip() or (f"https://{subscriber_id}/ondc" if subscriber_id else "")
    return subscriber_id, bap_uri


def new_context(
    action: str,
    *,
    domain: str,
    bap_id: str,
    bap_uri: str,
    country: str = "IND",
    city: str = "std:080",
    transaction_id: str | None = None,
    message_id: str | None = None,
    bpp_id: str | None = None,
    bpp_uri: str | None = None,
    core_version: str | None = None,
    ttl: str = "PT30S",
) -> dict[str, Any]:
    """Build a Beckn context. ``transaction_id`` is reused across a transaction;
    ``message_id`` is fresh per request. bpp_id/bpp_uri are set for directed
    actions (select/init/confirm/...) and omitted for ``search``."""
    ver = core_version or DEFAULT_CORE_VERSION
    context: dict[str, Any] = {
        "domain": domain,
        "country": country,
        "city": city,
        "action": action,
        # 1.2.x carries "core_version"; 2.x carries "version".
        ("core_version" if ver.startswith("1.") else "version"): ver,
        "bap_id": bap_id,
        "bap_uri": bap_uri,
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "message_id": message_id or str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "ttl": ttl,
    }
    if bpp_id:
        context["bpp_id"] = bpp_id
    if bpp_uri:
        context["bpp_uri"] = bpp_uri
    return context
