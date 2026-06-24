"""ONDC RSF 2.0 (Reconciliation & Settlement) — recon (11A) + on_recon (11B).

Separate subsystem from retail: domain ``ONDC:NTS10``, version ``2.0.0``, and a
NESTED location context (location.country.code / location.city.code) — unlike the
retail top-level country/city. Flow 11A: the BAP collector sends ``recon`` to a
receiver. Flow 11B: the BAP receiver answers an inbound ``recon`` with ``on_recon``
to the collector's bap_uri (must land within 5 min; fired async after the ACK).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from companion_api.ondc_client import post_signed
from companion_api.ondc_context import bap_identity
from companion_api.ondc_crypto import load_ondc_keys

logger = logging.getLogger(__name__)

RSF_DOMAIN = "ONDC:NTS10"
RSF_VERSION = "2.0.0"


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _money(value: str, currency: str = "INR") -> dict[str, str]:
    return {"currency": currency, "value": value}


def _rsf_context(
    action: str, *, bap_id: str, bap_uri: str, bpp_id: str, bpp_uri: str,
    transaction_id: str | None = None, message_id: str | None = None, city: str = "std:080",
) -> dict[str, Any]:
    """RSF context: NTS10 domain, version 2.0.0, nested location (on_recon pins city '*')."""
    return {
        "domain": RSF_DOMAIN,
        "location": {"country": {"code": "IND"}, "city": {"code": "*" if action == "on_recon" else city}},
        "version": RSF_VERSION,
        "action": action,
        "bap_id": bap_id,
        "bap_uri": bap_uri,
        "bpp_id": bpp_id,
        "bpp_uri": bpp_uri,
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "message_id": message_id or str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "ttl": "PT30S",
    }


def _settlement(order_id: str) -> dict[str, Any]:
    return {
        "id": "SETTLE-1",
        "payment_id": f"PAY-{order_id}",
        "status": "PENDING",
        "settlement_ref_no": f"SREF-{order_id}",
        "amount": _money("950.00"),
        "commission": _money("30.00"),
        "withholding_amount": _money("10.00"),
        "tcs": _money("5.00"),
        "tds": _money("5.00"),
        "updated_at": _now_iso(),
    }


def recon_message(order_id: str, *, value: str = "1000.00") -> dict[str, Any]:
    """Build a ``recon`` message — one order with a single settlement record."""
    return {"orders": [{"id": order_id, "amount": _money(value), "settlements": [_settlement(order_id)]}]}


def on_recon_message(inbound: dict[str, Any], *, accord: bool = True) -> dict[str, Any]:
    """Echo the inbound recon's orders and add ``recon_accord`` (Flow 11B accept)."""
    orders = []
    for o in (inbound.get("message") or {}).get("orders") or []:
        order = json.loads(json.dumps(o))
        order["recon_accord"] = accord
        orders.append(order)
    return {"orders": orders}


def send_recon(*, bpp_id: str, bpp_uri: str, order_id: str, transaction_id: str | None = None) -> dict[str, Any]:
    """Flow 11A: collector sends ``recon`` to the receiver (bpp)."""
    bap_id, bap_uri = bap_identity(load_ondc_keys())
    ctx = _rsf_context("recon", bap_id=bap_id, bap_uri=bap_uri, bpp_id=bpp_id, bpp_uri=bpp_uri, transaction_id=transaction_id)
    url = f"{bpp_uri.rstrip('/')}/recon"
    result = post_signed({"context": ctx, "message": recon_message(order_id)}, url)
    logger.warning("ONDC RSF recon -> sent %s status=%s txn=%s", url, result.get("status_code"), ctx["transaction_id"])
    return {"context": ctx, **result}


def respond_on_recon(inbound: dict[str, Any]) -> dict[str, Any] | None:
    """Flow 11B: answer an inbound ``recon`` with ``on_recon`` to the collector's bap_uri."""
    ctx_in = inbound.get("context") or {}
    bap_uri = ctx_in.get("bap_uri")
    if not bap_uri:
        logger.warning("ONDC RSF on_recon: inbound recon missing bap_uri")
        return None
    ctx = _rsf_context(
        "on_recon", bap_id=ctx_in.get("bap_id", ""), bap_uri=bap_uri,
        bpp_id=ctx_in.get("bpp_id", ""), bpp_uri=ctx_in.get("bpp_uri", ""),
        transaction_id=ctx_in.get("transaction_id"), message_id=ctx_in.get("message_id"),
    )
    url = f"{bap_uri.rstrip('/')}/on_recon"
    result = post_signed({"context": ctx, "message": on_recon_message(inbound)}, url)
    logger.warning("ONDC RSF on_recon -> sent %s status=%s", url, result.get("status_code"))
    return result
