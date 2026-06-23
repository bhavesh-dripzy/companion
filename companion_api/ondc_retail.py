"""ONDC retail (RET1x) message builders for the BAP action sequence.

Builds the ``message`` bodies for the discovery half of the flow: ``search``
(intent by item name + delivery location + finder fee) and ``select`` (the
chosen provider + items from an on_search). Exact tag/quote shapes are
version-sensitive (1.2.x vs 2.x) and MUST be validated against Pramaan once the
subscriber is live; init/confirm builders are added against that feedback.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

DEFAULT_FINDER_FEE_PCT = os.getenv("ONDC_FINDER_FEE_PCT", "3.0")

# Test buyer identity for Pramaan mock-seller flows (override via env).
TEST_BUYER_NAME = os.getenv("ONDC_TEST_BUYER_NAME", "ONDC Test Buyer")
TEST_BUYER_PHONE = os.getenv("ONDC_TEST_BUYER_PHONE", "9876543210")
TEST_BUYER_EMAIL = os.getenv("ONDC_TEST_BUYER_EMAIL", "buyer@jatayulabs.com")
TEST_GPS = os.getenv("ONDC_TEST_GPS", "12.9719,77.5937")
TEST_AREA_CODE = os.getenv("ONDC_TEST_AREA_CODE", "560001")


def _delivery_end(gps: str | None, area_code: str | None) -> dict[str, Any]:
    location: dict[str, Any] = {}
    if gps:
        location["gps"] = gps
    if area_code:
        location["address"] = {"area_code": area_code}
    return {"location": location}


def search_intent(
    query: str = "",
    *,
    gps: str | None = None,
    area_code: str | None = None,
    category_id: str | None = None,
    finder_fee_pct: str | None = None,
) -> dict[str, Any]:
    """Build a RET search ``message`` — by item name and/or category, delivered."""
    intent: dict[str, Any] = {}
    if query:
        intent["item"] = {"descriptor": {"name": query}}
    if category_id:
        intent["category"] = {"id": category_id}
    intent["fulfillment"] = {"type": "Delivery", "end": _delivery_end(gps, area_code)}
    intent["payment"] = {
        "@ondc/org/buyer_app_finder_fee_type": "percent",
        "@ondc/org/buyer_app_finder_fee_amount": finder_fee_pct or DEFAULT_FINDER_FEE_PCT,
    }
    return {"intent": intent}


def select_message(
    provider_id: str,
    item_ids: list[str],
    *,
    location_ids: list[str] | None = None,
    fulfillment_id: str | None = None,
    gps: str | None = None,
    area_code: str | None = None,
    quantities: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a ``select`` ``message`` for chosen items from one provider."""
    quantities = quantities or {}
    items = [
        {
            "id": item_id,
            "quantity": {"count": quantities.get(item_id, 1)},
            **({"fulfillment_id": fulfillment_id} if fulfillment_id else {}),
        }
        for item_id in item_ids
    ]
    order: dict[str, Any] = {
        "provider": {"id": provider_id},
        "items": items,
        "fulfillments": [{"type": "Delivery", "end": _delivery_end(gps, area_code)}],
    }
    if location_ids:
        order["provider"]["locations"] = [{"id": loc} for loc in location_ids]
    return {"order": order}


def _address(area_code: str | None) -> dict[str, Any]:
    return {
        "name": TEST_BUYER_NAME,
        "building": "Test Building",
        "locality": "Test Locality",
        "city": "Bengaluru",
        "state": "Karnataka",
        "country": "IND",
        "area_code": area_code or TEST_AREA_CODE,
    }


def billing(*, created_at: str, area_code: str | None = None) -> dict[str, Any]:
    """Spec-shaped billing block; timestamps MUST stay byte-identical init→confirm."""
    return {
        "name": TEST_BUYER_NAME,
        "address": _address(area_code),
        "phone": TEST_BUYER_PHONE,
        "email": TEST_BUYER_EMAIL,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _fulfillment_end(gps: str | None, area_code: str | None) -> dict[str, Any]:
    return {
        "location": {"gps": gps or TEST_GPS, "address": _address(area_code)},
        "contact": {"phone": TEST_BUYER_PHONE},
    }


def init_message(
    provider_id: str,
    item_ids: list[str],
    *,
    created_at: str,
    location_ids: list[str] | None = None,
    fulfillment_id: str | None = None,
    gps: str | None = None,
    area_code: str | None = None,
    quantities: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build an ``init`` message — order + billing + delivery fulfillment (no quote/payment yet)."""
    quantities = quantities or {}
    items = [
        {
            "id": item_id,
            "quantity": {"count": quantities.get(item_id, 1)},
            **({"fulfillment_id": fulfillment_id} if fulfillment_id else {}),
        }
        for item_id in item_ids
    ]
    provider: dict[str, Any] = {"id": provider_id}
    if location_ids:
        provider["locations"] = [{"id": loc} for loc in location_ids]
    fulfillment: dict[str, Any] = {"type": "Delivery", "end": _fulfillment_end(gps, area_code)}
    if fulfillment_id:
        fulfillment["id"] = fulfillment_id
    return {
        "order": {
            "provider": provider,
            "items": items,
            "billing": billing(created_at=created_at, area_code=area_code),
            "fulfillments": [fulfillment],
        }
    }


def _confirm_payment(on_init_payment: dict[str, Any], *, amount: str, currency: str, ref: str) -> dict[str, Any]:
    """Prepaid (ON-ORDER, collected by BAP), preserving the seller's @ondc/org settlement terms."""
    payment = dict(on_init_payment or {})
    payment.setdefault("type", "ON-ORDER")
    payment["collected_by"] = payment.get("collected_by") or "BAP"
    payment["status"] = "PAID"
    payment["params"] = {"currency": currency, "amount": amount, "transaction_id": ref}
    payment.setdefault("@ondc/org/buyer_app_finder_fee_type", "percent")
    payment.setdefault("@ondc/org/buyer_app_finder_fee_amount", DEFAULT_FINDER_FEE_PCT)
    payment.setdefault("@ondc/org/settlement_basis", "delivery")
    payment.setdefault("@ondc/org/settlement_window", "P1D")
    payment.setdefault("@ondc/org/withholding_amount", "0.00")
    return payment


def confirm_message(on_init_order: dict[str, Any], *, created_at: str, order_id: str | None = None) -> dict[str, Any]:
    """Build a ``confirm`` from the on_init order: set id/state=Created, mark PAID, accept bpp_terms."""
    order = json.loads(json.dumps(on_init_order or {}))
    order["id"] = order.get("id") or order_id or str(uuid.uuid4())
    order["state"] = "Created"
    price = (order.get("quote") or {}).get("price") or {}
    order["payment"] = _confirm_payment(
        order.get("payment") or {},
        amount=price.get("value", "0"),
        currency=price.get("currency", "INR"),
        ref=order["id"],
    )
    tags = order.get("tags") or []
    tags = tags if isinstance(tags, list) else [tags]
    tags = [t for t in tags if not (isinstance(t, dict) and t.get("code") == "bap_terms")]
    tags.append({"code": "bap_terms", "list": [{"code": "accept_bpp_terms", "value": "Y"}]})
    order["tags"] = tags
    order["created_at"] = created_at
    order["updated_at"] = created_at
    return {"order": order}


def status_message(order_id: str) -> dict[str, Any]:
    """Build a ``status`` message (order_id is the only mandatory field)."""
    return {"order_id": order_id}


def track_message(order_id: str) -> dict[str, Any]:
    """Build a ``track`` message (order_id is the only mandatory field)."""
    return {"order_id": order_id}


def cancel_message(order_id: str, reason_id: str = "001") -> dict[str, Any]:
    """Build a ``cancel`` message. reason_id default 001 = delivery delayed/not possible."""
    return {"order_id": order_id, "cancellation_reason_id": reason_id}
