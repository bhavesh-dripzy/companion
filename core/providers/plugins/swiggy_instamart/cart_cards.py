"""Swiggy Instamart cart-card builder (clean port of `_instamart_cart_snapshot`).

A pure function of a `get_cart` / `update_cart` envelope. The legacy enriched
missing item names/images from the session_products LRU; here we read them from the
envelope (which carries displayName/imageUrl/quantityDescription) — the session
fallback is a minor deferred nicety (rarely needed; the cart envelope is detailed).
Empty/expired carts surface as is_error on this MCP, so they naturally render no card.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.plugins.swiggy_instamart.cards import _coerce_price_inr

_CART_TOOLS = {"get_cart", "update_cart", "clear_cart"}


def _items_raw(data: dict[str, Any]) -> list[Any]:
    for key in ("items", "cartItems", "cart_items"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    inner = data.get("cart") if isinstance(data.get("cart"), dict) else None
    if inner:
        for key in ("items", "cartItems", "cart_items"):
            v = inner.get(key)
            if isinstance(v, list):
                return v
    return []


def _cart_item(it: Any) -> tuple[dict[str, Any], float | None, int] | None:
    if not isinstance(it, dict):
        return None
    spin = str(it.get("spinId") or it.get("spin_id") or it.get("variantId") or "").strip() or None
    try:
        qty = int(it.get("quantity") or it.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1
    offer_inr, mrp_inr = _coerce_price_inr(it.get("price"))
    if offer_inr is None:
        for k in ("offerPrice", "offer_price", "price_inr", "price"):
            v = it.get(k)
            if isinstance(v, int | float) and not isinstance(v, bool):
                offer_inr = float(v)
                break
    name = str(it.get("displayName") or "").strip() or str(it.get("name") or "").strip() or None
    row = {
        "spin_id": spin,
        "quantity": qty,
        "name": name,
        "brand": str(it.get("brand") or "").strip() or None,
        "price_inr": offer_inr,
        "mrp_inr": mrp_inr,
        "image_url": str(it.get("imageUrl") or "").strip() or None,
        "quantity_description": str(it.get("quantityDescription") or "").strip() or None,
    }
    return row, offer_inr, qty


def _grand_total_paise(data: dict[str, Any], computed: int, has_priced: bool) -> int | None:
    for src in (data, data.get("bill") if isinstance(data.get("bill"), dict) else None):
        if not isinstance(src, dict):
            continue
        for k in ("grandTotalPaise", "grand_total_paise", "billTotalPaise", "totalPaise"):
            if isinstance(src.get(k), int | float):
                return int(src[k])
        for k in ("grandTotal", "billTotal", "total", "subtotal"):
            if isinstance(src.get(k), int | float):
                return int(round(float(src[k]) * 100))
    return computed if has_priced else None


def cart_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    items: list[dict[str, Any]] = []
    computed, has_priced = 0, False
    for it in _items_raw(data):
        parsed = _cart_item(it)
        if parsed is None:
            continue
        row, price, qty = parsed
        if isinstance(price, int | float):
            computed += int(round(float(price) * 100)) * qty
            has_priced = True
        items.append(row)
    if not items:
        return None
    selected_address_id = None
    sel = data.get("selectedAddressDetails")
    if isinstance(sel, dict):
        aid = sel.get("id") or sel.get("addressId") or sel.get("address_id")
        selected_address_id = str(aid).strip() or None if aid else None
    return {
        "is_empty": False,
        "items": items,
        "grand_total_paise": _grand_total_paise(data, computed, has_priced),
        "selected_address_id": selected_address_id,
    }


def build_cart_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _CART_TOOLS or not tr.ok:
            continue
        snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {})
        if snap and snap.get("items"):
            out.append(
                Attachment(
                    kind="instamart_cart_card",
                    provider="swiggy_instamart",
                    source=f"swiggy_instamart.{tr.tool}",
                    instamart_cart=snap,
                )
            )
    return out
