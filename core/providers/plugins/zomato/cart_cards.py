"""Zomato cart-card builder (clean port of the legacy cart snapshot + collector).

Ported from `zomato.mcp_executor._extract_cart_snapshot` and
`agents.pipeline._build_cart_card_attachment` / `_collect_cart_attachments`,
adapted to operate on `ToolResult`s. The snapshot is a pure function of a
successful `create_cart` envelope (Zomato carts are single-restaurant: each
create_cart returns a fresh cart_id and discards the previous one), so the card
builds directly from the raw envelope — no executor state needed.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_CART_TOOLS = {"create_cart"}


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _cart_item(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    vid = str(item.get("variant_id") or row.get("variant_id") or "").strip()
    name = str(item.get("name") or row.get("item_name") or "").strip()
    if not vid or not name:
        return None
    addons: list[dict[str, Any]] = []
    for ao in row.get("add_ons") or []:
        if not isinstance(ao, dict):
            continue
        ao_name = str(ao.get("name") or "").strip()
        if ao_name:
            addons.append(
                {
                    "name": ao_name,
                    "add_on_group_name": str(ao.get("add_on_group_name") or "").strip() or None,
                    "quantity": ao.get("quantity"),
                }
            )
    tags = item.get("item_tags")
    tags = (
        [str(t).strip() for t in tags if isinstance(t, str) and t.strip()]
        if isinstance(tags, list)
        else []
    )
    return {
        "variant_id": vid,
        "item_id": str(item.get("item_id") or "").strip() or None,
        "item_name": name,
        "quantity": row.get("quantity"),
        "price": item.get("price"),
        "discounted_price": item.get("discounted_price"),
        "description": str(item.get("description") or "").strip() or None,
        "image_url": str(item.get("image_url") or "").strip() or None,
        "item_tags": tags,
        "add_ons": addons,
    }


def cart_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a successful create_cart envelope into a renderable cart snapshot."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    cart = next(
        (
            n
            for n in _walk(data)
            if isinstance(n, dict) and n.get("cart_id") and isinstance(n.get("items"), list)
        ),
        None,
    )
    if not cart:
        return None
    items = [c for c in (_cart_item(r) for r in cart.get("items") or []) if c]
    if not items:
        return None
    cb = cart.get("charge_breakdown") if isinstance(cart.get("charge_breakdown"), dict) else None
    charge_breakdown = None
    if cb:
        charge_breakdown = {
            "base_charges": cb.get("base_charges")
            if isinstance(cb.get("base_charges"), list)
            else [],
            "taxes": cb.get("taxes") if isinstance(cb.get("taxes"), list) else [],
        }
    res_id = cart.get("res_id")
    user_message = cart.get("user_message")
    return {
        "cart_id": str(cart.get("cart_id") or "").strip() or None,
        "res_id": str(res_id).strip() if res_id is not None else None,
        "restaurant_name": str(cart.get("restaurant_name") or "").strip() or None,
        "restaurant_rating": cart.get("restaurant_rating"),
        "distance_in_km": cart.get("distance_in_km"),
        "items": items,
        "item_total": cart.get("item_total"),
        "final_amount": cart.get("final_amount"),
        "payment_method_type": str(cart.get("payment_method_type") or "").strip() or None,
        "eta": str(cart.get("eta") or "").strip() or None,
        "charge_breakdown": charge_breakdown,
        "user_message": user_message.strip()
        if isinstance(user_message, str) and user_message.strip()
        else None,
        "shareable_link": str(cart.get("shareable_link") or "").strip() or None,
        "promo_code": cart.get("promo_code"),
        "promo_discount_amount": cart.get("promo_discount_amount"),
    }


def build_cart_cards(results: list[ToolResult], *, provider: str = "zomato") -> list[Attachment]:
    """One cart_card per successful create_cart this turn (needs cart_id + ≥1 item)."""
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _CART_TOOLS or not tr.ok:
            continue
        snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {})
        if not snap or not snap.get("cart_id") or not snap.get("items"):
            continue
        out.append(
            Attachment(
                kind="cart_card",
                source=f"{provider}.{tr.tool}",
                provider=provider,
                cart=snap,
            )
        )
    return out
