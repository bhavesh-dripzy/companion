"""ONDC IGM 2.0 (Issue & Grievance Management) — Flows 6A-6F.

Rides on the retail domain ``ONDC:RET10`` but with ``core_version 2.0.0`` and the
IGM 2.0 issue model (refs/actors/actions/resolutions). We send ``issue`` and
receive ``on_issue`` (ACK + record). 6A opens an issue; 6B-6E are distinguished by
the seller's on_issue resolution (we may accept it); 6F escalates to GRIEVANCE.

issue lifecycle invariants enforced by the validator we satisfy here: source_id ==
the CONSUMER actor id; complainant_id == the INTERFACING_NP actor id; last_action_id
== the last actions[] id; for an OPEN, status==OPEN and updated_at==created_at.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from companion_api.ondc_client import post_signed
from companion_api.ondc_context import bap_identity
from companion_api.ondc_crypto import load_ondc_keys

logger = logging.getLogger(__name__)

IGM_DOMAIN = "ONDC:RET10"
IGM_VERSION = "2.0.0"
_BUYER_PHONE = "9876543210"
_BUYER_EMAIL = "buyer@jatayulabs.com"
_GRO_PHONE = "9000000000"
_GRO_EMAIL = "grievance@jatayulabs.com"


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _igm_context(
    action: str, *, bap_id: str, bap_uri: str, bpp_id: str, bpp_uri: str,
    transaction_id: str | None = None, message_id: str | None = None, city: str = "std:080",
) -> dict[str, Any]:
    """IGM 2.0 context on RET: core_version 2.0.0 + top-level country/city."""
    return {
        "domain": IGM_DOMAIN,
        "country": "IND",
        "city": city,
        "action": action,
        "core_version": IGM_VERSION,
        "bap_id": bap_id,
        "bap_uri": bap_uri,
        "bpp_id": bpp_id,
        "bpp_uri": bpp_uri,
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "message_id": message_id or str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "ttl": "PT30S",
    }


def _actors(bap_id: str, bpp_id: str) -> list[dict[str, Any]]:
    return [
        {"id": "CONSUMER-1", "type": "CONSUMER",
         "info": {"org": {"name": bap_id}, "person": {"name": "ONDC Test Buyer"},
                  "contact": {"phone": _BUYER_PHONE, "email": _BUYER_EMAIL}}},
        {"id": "INP-1", "type": "INTERFACING_NP",
         "info": {"org": {"name": bap_id}, "contact": {"phone": _GRO_PHONE, "email": _GRO_EMAIL}}},
        {"id": "CNP-1", "type": "COUNTERPARTY_NP",
         "info": {"org": {"name": bpp_id}, "contact": {"phone": "9111111111", "email": f"support@{bpp_id or 'seller'}"}}},
    ]


def _refs(*, order_id: str, provider_id: str, fulfillment_id: str | None, item_id: str, order_txn: str) -> list[dict[str, Any]]:
    return [
        {"ref_id": order_id, "ref_type": "ORDER"},
        {"ref_id": provider_id, "ref_type": "PROVIDER"},
        {"ref_id": fulfillment_id or "F1", "ref_type": "FULFILLMENT"},
        {"ref_id": item_id, "ref_type": "ITEM",
         "tags": [{"descriptor": {"code": "message.order.items"},
                   "list": [{"descriptor": {"code": "quantity.selected.count"}, "value": "1"}]}]},
        {"ref_id": order_txn, "ref_type": "TRANSACTION"},
    ]


def build_issue(
    state: dict[str, Any], order_txn: str, *,
    issue_id: str | None = None, category: str = "Item", sub_category: str = "ITM02",
    short_desc: str = "Item received in damaged condition",
    long_desc: str = "The product delivered was physically damaged on arrival.",
    level: str = "ISSUE", action_code: str = "OPEN", status: str = "OPEN",
) -> dict[str, Any]:
    """Build an IGM 2.0 ``issue`` message. Defaults open a fresh ITEM issue (6A);
    level=GRIEVANCE + action_code=ESCALATED drives escalation (6F)."""
    bap_id, _ = bap_identity(load_ondc_keys())
    bpp_id = state.get("bpp_id", "")
    now = _now_iso()
    action_id = "ACTION-1"
    issue = {
        "id": issue_id or str(uuid.uuid4()),
        "status": status,
        "level": level,
        "category": category,
        "sub_category": sub_category,
        "created_at": now,
        "updated_at": now,
        "expected_response_time": {"duration": "PT1H"},
        "expected_resolution_time": {"duration": "P1D"},
        "refs": _refs(
            order_id=state.get("order_id", ""), provider_id=state.get("provider_id", "P1"),
            fulfillment_id=state.get("fulfillment_id"), item_id=(state.get("item_ids") or ["I1"])[0],
            order_txn=order_txn,
        ),
        "actors": _actors(bap_id, bpp_id),
        "source_id": "CONSUMER-1",
        "complainant_id": "INP-1",
        "descriptor": {"code": sub_category, "short_desc": short_desc, "long_desc": long_desc},
        "last_action_id": action_id,
        "actions": [{
            "id": action_id, "descriptor": {"code": action_code, "short_desc": short_desc},
            "updated_at": now, "action_by": "INP-1", "actor_details": {"name": bap_id},
        }],
    }
    return {"issue": issue}


def build_issue_close(state: dict[str, Any], order_txn: str, *, issue_id: str, rating: str = "THUMBS-UP") -> dict[str, Any]:
    """Build a CLOSE ``issue`` (IGM 2.0): status CLOSED + a CLOSE action with mandatory tags (rating)."""
    msg = build_issue(state, order_txn, issue_id=issue_id, action_code="CLOSED", status="CLOSED",
                      short_desc="Issue closed by buyer")
    msg["issue"]["actions"][-1]["tags"] = [
        {"descriptor": {"code": "rating"}, "list": [{"descriptor": {"code": "value"}, "value": rating}]}
    ]
    return msg


def send_issue(order_txn: str, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Flow 6A/6F: send an ``issue`` to the seller (bpp). ``params`` may set
    category/sub_category/level/action_code/short_desc/long_desc/close/rating."""
    bap_id, bap_uri = bap_identity(load_ondc_keys())
    bpp_id, bpp_uri = state.get("bpp_id", ""), state.get("bpp_uri", "")
    issue_id = params.get("issue_id") or str(uuid.uuid4())
    if params.get("close"):
        message = build_issue_close(state, order_txn, issue_id=issue_id, rating=params.get("rating", "THUMBS-UP"))
    else:
        message = build_issue(
            state, order_txn, issue_id=issue_id,
            category=params.get("category", "Item"), sub_category=params.get("sub_category", "ITM02"),
            level=params.get("level", "ISSUE"), action_code=params.get("action_code", "OPEN"),
        )
    ctx = _igm_context("issue", bap_id=bap_id, bap_uri=bap_uri, bpp_id=bpp_id, bpp_uri=bpp_uri,
                       transaction_id=params.get("igm_txn") or str(uuid.uuid4()))
    url = f"{bpp_uri.rstrip('/')}/issue"
    result = post_signed({"context": ctx, "message": message}, url)
    logger.warning("ONDC IGM issue -> sent %s status=%s issue_id=%s", url, result.get("status_code"), issue_id)
    return {"context": ctx, "issue_id": issue_id, **result}
