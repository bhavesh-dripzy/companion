"""Zepto cart-card builder (clean port of `_harvest_cart_snapshot`).

A pure function of a `view_cart` envelope's structured payload
(`{items, isEmpty, totalItems}`). Zepto's cart items carry their own
name/packSize/imageUrl, so this is self-contained (the legacy's session_products
fallback is a minor deferred nicety). Prices are paise → rupees, consistent with
the product cards. The prose-fallback parser is not ported — the hosted server
returns structured content.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_CART_TOOLS = {"view_cart", "update_cart"}


def _structured(envelope: dict[str, Any]) -> dict[str, Any] | None:
    data = envelope.get("data")
    if not isinstance(data, dict) or set(data.keys()) == {"raw_text"}:
        return None
    return data


def _cart_item(it: Any) -> tuple[dict[str, Any], int, int] | None:
    if not isinstance(it, dict):
        return None
    pvid = (
        it.get("productVariantId")
        or it.get("product_variant_id")
        or it.get("variantId")
        or it.get("pvid")
    )
    spid = it.get("storeProductId") or it.get("store_product_id") or it.get("spid")
    try:
        qty = int(it.get("quantity") or it.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1
    price_paise = it.get("price")
    price_inr = round(float(price_paise) / 100, 2) if isinstance(price_paise, int | float) else None
    row = {
        "product_variant_id": pvid,
        "store_product_id": spid,
        "quantity": qty,
        "name": it.get("name"),
        "price_inr": price_inr,
        "price_text": f"₹{price_inr:g}" if price_inr is not None else None,
        "pack_size": it.get("packSize") or it.get("pack_size"),
        "image_url": it.get("imageUrl") or it.get("image_url"),
        "label": it.get("label") or None,
        "available_quantity": it.get("availableQuantity"),
    }
    paise = int(price_paise) if isinstance(price_paise, int | float) else 0
    return row, paise, qty


def cart_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    blob = _structured(envelope)
    if not isinstance(blob, dict):
        return None
    raw_items = blob.get("items") or blob.get("cartItems") or []
    items: list[dict[str, Any]] = []
    computed, has_priced = 0, False
    for it in raw_items if isinstance(raw_items, list) else []:
        parsed = _cart_item(it)
        if parsed is None:
            continue
        row, paise, qty = parsed
        if paise:
            computed += paise * qty
            has_priced = True
        items.append(row)
    if not items:
        return None
    total = None
    for k in ("grandTotalAmount", "totalAmount", "subtotal", "cartTotal"):
        if isinstance(blob.get(k), int | float):
            total = int(blob[k])
            break
    if total is None and has_priced:
        total = computed
    return {"is_empty": False, "items": items, "grand_total_paise": total}


def build_cart_cards(
    results: list[ToolResult],
    *,
    address_id: str | None = None,
    addresses: list[dict] | None = None,
) -> list[Attachment]:
    # Always carry the committed delivery address WITH the cart so the UI shows
    # where it ships (Zepto binds the address at the session level, so the turn's
    # resolved/committed address is authoritative — there's no per-cart address).
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _CART_TOOLS or not tr.ok:
            continue
        snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {})
        if snap and snap.get("items"):
            out.append(
                Attachment(
                    kind="zepto_cart_card",
                    provider="zepto",
                    source=f"zepto.{tr.tool}",
                    zepto_cart=snap,
                    delivery_address_id=address_id,
                    delivery_addresses=addresses or None,
                )
            )
    return out
