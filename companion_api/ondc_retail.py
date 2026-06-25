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
from datetime import datetime, timedelta, timezone
from typing import Any


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _iso_ago(seconds: int) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

DEFAULT_FINDER_FEE_PCT = os.getenv("ONDC_FINDER_FEE_PCT", "3.0")

# Test buyer identity for Pramaan mock-seller flows (override via env).
TEST_BUYER_NAME = os.getenv("ONDC_TEST_BUYER_NAME", "ONDC Test Buyer")
TEST_BUYER_PHONE = os.getenv("ONDC_TEST_BUYER_PHONE", "9876543210")
TEST_BUYER_EMAIL = os.getenv("ONDC_TEST_BUYER_EMAIL", "buyer@jatayulabs.com")
TEST_GPS = os.getenv("ONDC_TEST_GPS", "12.971599,77.594563")
TEST_AREA_CODE = os.getenv("ONDC_TEST_AREA_CODE", "560001")
# 15-char GSTIN (state 29 = Karnataka) for the BAP's bap_terms tax_number.
BAP_GST = os.getenv("ONDC_BAP_GST", "29AAACB2230M1ZX")


def _delivery_end(gps: str | None, area_code: str | None) -> dict[str, Any]:
    location: dict[str, Any] = {}
    if gps:
        location["gps"] = gps
    if area_code:
        location["address"] = {"area_code": area_code}
    return {"location": location}


def _search_tags(search_type: str | None) -> list[dict[str, Any]] | None:
    """Catalog tags that mark a search as full (8A) / incremental pull (8B) / push (8C)."""
    if search_type == "full":
        return [{"code": "catalog_full", "list": [{"code": "payload_type", "value": "inline"}]}]
    if search_type == "inc_pull":
        return [{"code": "catalog_inc", "list": [
            {"code": "start_time", "value": _iso_ago(3600)},
            {"code": "end_time", "value": _now_iso()},
        ]}]
    if search_type == "inc_push":
        return [{"code": "catalog_inc", "list": [{"code": "mode", "value": "start"}]}]
    return None


def search_intent(
    query: str = "",
    *,
    gps: str | None = None,
    area_code: str | None = None,
    category_id: str | None = None,
    finder_fee_pct: str | None = None,
    search_type: str | None = None,
) -> dict[str, Any]:
    """Build a RET search ``message`` — by item name and/or category, delivered.

    ``search_type`` adds catalog tags for the discovery flows: full (8A),
    inc_pull (8B, start/end window), inc_push (8C, mode=start subscription).
    """
    intent: dict[str, Any] = {}
    if query:
        intent["item"] = {"descriptor": {"name": query}}
    intent["category"] = {"id": category_id or "Foodgrains"}
    intent["fulfillment"] = {"type": "Delivery", "end": _delivery_end(gps, area_code)}
    intent["payment"] = {
        "@ondc/org/buyer_app_finder_fee_type": "percent",
        "@ondc/org/buyer_app_finder_fee_amount": finder_fee_pct or DEFAULT_FINDER_FEE_PCT,
    }
    intent["tags"] = _search_tags(search_type) or [
        {"code": "bap_terms", "list": [
            {"code": "finder_fee_type", "value": "percent"},
            {"code": "finder_fee_amount", "value": finder_fee_pct or DEFAULT_FINDER_FEE_PCT},
        ]}
    ]
    return {"intent": intent}


def catalog_rejection_errors(
    *,
    provider_index: int = 0,
    item_index: int = 0,
    code: str = "30016",
    message: str = "Mandatory catalog attribute missing for item",
) -> list[dict[str, Any]]:
    """Build the ``errors`` array for a catalog_rejection (Flow 9) — rejects one item by path."""
    return [{
        "code": code,
        "type": "ITEM-ERROR",
        "path": f"message.catalog.bpp/providers[{provider_index}].items[{item_index}]",
        "message": message,
    }]


def select_message(
    provider_id: str,
    item_ids: list[str],
    *,
    location_ids: list[str] | None = None,
    fulfillment_id: str | None = None,
    location_id: str | None = None,
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
            **({"location_id": location_id} if location_id else {}),
            **({"fulfillment_id": fulfillment_id} if fulfillment_id else {}),
        }
        for item_id in item_ids
    ]
    order: dict[str, Any] = {
        "provider": {"id": provider_id},
        "items": items,
        "fulfillments": [{"type": "Delivery", "end": _delivery_end(gps, area_code)}],
        "payment": {"type": "ON-ORDER", "collected_by": "BAP"},
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
        "tax_number": BAP_GST,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _fulfillment_end(gps: str | None, area_code: str | None) -> dict[str, Any]:
    return {
        "location": {"gps": gps or TEST_GPS, "address": _address(area_code)},
        "contact": {"phone": TEST_BUYER_PHONE, "email": TEST_BUYER_EMAIL},
        "person": {"name": TEST_BUYER_NAME},
    }


def init_message(
    provider_id: str,
    item_ids: list[str],
    *,
    created_at: str,
    location_ids: list[str] | None = None,
    fulfillment_id: str | None = None,
    location_id: str | None = None,
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
            **({"location_id": location_id} if location_id else {}),
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
    order["id"] = order.get("id") or order_id or uuid.uuid4().hex
    order["state"] = "Created"
    price = (order.get("quote") or {}).get("price") or {}
    order["payment"] = _confirm_payment(
        order.get("payment") or {},
        amount=price.get("value", "0"),
        currency=price.get("currency", "INR"),
        ref=order["id"],
    )
    for fulfillment in order.get("fulfillments") or []:
        if "tracking" not in fulfillment:
            fulfillment["tracking"] = True
        fulfillment.setdefault("@ondc/org/TAT", "PT1H")
        end = fulfillment.setdefault("end", {})
        end.setdefault("person", {"name": TEST_BUYER_NAME})
        end.setdefault("contact", {"phone": TEST_BUYER_PHONE, "email": TEST_BUYER_EMAIL})
    tags = order.get("tags") or []
    tags = tags if isinstance(tags, list) else [tags]
    tags = [t for t in tags if not (isinstance(t, dict) and t.get("code") == "bap_terms")]
    tags.append({"code": "bap_terms", "list": [{"code": "tax_number", "value": BAP_GST}]})
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


def cancel_message(order_id: str, reason_id: str = "051") -> dict[str, Any]:
    """Build a ``cancel`` message. Default 051 = buyer-initiated (valid buyerCancellationRid;
    ONDC buyer-cancel codes are 051/052/053/999/010 — 001 is a seller code)."""
    return {"order_id": order_id, "cancellation_reason_id": reason_id}


def update_return_message(
    order_id: str,
    item_ids: list[str] | None = None,
    *,
    reason_id: str = "001",
    quantity: str = "1",
) -> dict[str, Any]:
    """Build an ``update`` message for a buyer-initiated return (update_target=item).

    Adds a Return-type fulfillment carrying a ``return_request`` tag for the item.
    """
    item_id = (item_ids or [""])[0]
    return {
        "update_target": "item",
        "order": {
            "id": order_id,
            "fulfillments": [
                {
                    "id": "return-1",
                    "type": "Return",
                    "tags": [
                        {
                            "code": "return_request",
                            "list": [
                                {"code": "id", "value": "1"},
                                {"code": "item_id", "value": item_id},
                                {"code": "item_quantity", "value": quantity},
                                {"code": "reason_id", "value": reason_id},
                            ],
                        }
                    ],
                }
            ],
        },
    }
