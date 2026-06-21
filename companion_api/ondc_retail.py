"""ONDC retail (RET1x) message builders for the BAP action sequence.

Builds the ``message`` bodies for the discovery half of the flow: ``search``
(intent by item name + delivery location + finder fee) and ``select`` (the
chosen provider + items from an on_search). Exact tag/quote shapes are
version-sensitive (1.2.x vs 2.x) and MUST be validated against Pramaan once the
subscriber is live; init/confirm builders are added against that feedback.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_FINDER_FEE_PCT = os.getenv("ONDC_FINDER_FEE_PCT", "3.0")


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
