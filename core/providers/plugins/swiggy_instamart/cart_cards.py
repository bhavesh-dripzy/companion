"""Swiggy Instamart cart-card builder.

A pure function of a ``get_cart`` / ``update_cart`` envelope. The live cart
payload carries items as ``{spinId, itemName, quantity, mrp, discountedFinalPrice,
isInStockAndAvailable}`` and the bill total as a rupee string (``cartTotalAmount``
/ ``billBreakdown.toPay.value``) — it does NOT carry product images or pack
descriptions, so those are enriched from the session products LRU (and this
turn's search results) by spinId. Empty/expired carts surface as is_error on
this MCP, so they naturally render no card.
"""

from __future__ import annotations

import re
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.plugins.swiggy_instamart.cards import _coerce_price_inr, products_from_search

_CART_TOOLS = {"get_cart", "update_cart", "clear_cart"}
_SEARCH_TOOLS = {"search_products", "your_go_to_items"}
_PRICE_RE = re.compile(r"[\d]+(?:\.\d+)?")


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


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def build_spin_index(
    session_products: Any, results: list[ToolResult] | None = None
) -> dict[str, dict[str, Any]]:
    """spinId -> {name, brand, image_url, quantity_description, price_inr, mrp_inr}.

    Built from the carried session LRU plus any search results this turn so cart
    items (which lack images) can be enriched.
    """
    index: dict[str, dict[str, Any]] = {}

    def absorb(rows: Any) -> None:
        if not isinstance(rows, list):
            return
        for prod in rows:
            if not isinstance(prod, dict):
                continue
            pname = str(prod.get("name") or "").strip() or None
            brand = str(prod.get("brand") or "").strip() or None
            for var in prod.get("variants") or []:
                if not isinstance(var, dict):
                    continue
                sid = str(var.get("spin_id") or "").strip()
                if not sid or sid in index:
                    continue
                index[sid] = {
                    "name": pname,
                    "brand": brand,
                    "image_url": var.get("image_url"),
                    "quantity_description": var.get("quantity_description"),
                    "price_inr": var.get("price_inr"),
                    "mrp_inr": var.get("mrp_inr"),
                }

    absorb(session_products)
    for tr in results or []:
        if tr.tool in _SEARCH_TOOLS and tr.ok:
            envelope = tr.data if isinstance(tr.data, dict) else {}
            query = str((tr.arguments or {}).get("query") or "").strip() or None
            absorb(products_from_search(envelope, query=query))
    return index


def _cart_item(it: Any, spin_index: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], float | None, int] | None:
    if not isinstance(it, dict):
        return None
    spin = str(it.get("spinId") or it.get("spin_id") or it.get("variantId") or "").strip() or None
    try:
        qty = int(it.get("quantity") or it.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1

    enrich = spin_index.get(spin or "", {})

    # Price: live cart uses flat `discountedFinalPrice` (offer) + `mrp`; fall back
    # to a nested price dict, then to the enriched LRU price.
    offer_inr = _num(it.get("discountedFinalPrice"))
    mrp_inr = _num(it.get("mrp"))
    if offer_inr is None or mrp_inr is None:
        d_offer, d_mrp = _coerce_price_inr(it.get("price"))
        offer_inr = offer_inr if offer_inr is not None else d_offer
        mrp_inr = mrp_inr if mrp_inr is not None else d_mrp
    if offer_inr is None:
        for k in ("offerPrice", "offer_price", "price_inr"):
            v = _num(it.get(k))
            if v is not None:
                offer_inr = v
                break
    if offer_inr is None:
        offer_inr = enrich.get("price_inr")
    if mrp_inr is None:
        mrp_inr = enrich.get("mrp_inr")

    # Name: live cart uses `itemName`; fall back to displayName/name, then LRU.
    name = (
        str(it.get("itemName") or "").strip()
        or str(it.get("displayName") or "").strip()
        or str(it.get("name") or "").strip()
        or enrich.get("name")
        or None
    )

    row = {
        "spin_id": spin,
        "quantity": qty,
        "name": name,
        "brand": str(it.get("brand") or "").strip() or enrich.get("brand"),
        "price_inr": offer_inr,
        "mrp_inr": mrp_inr,
        # Image is never in the cart payload — enrich from the LRU.
        "image_url": str(it.get("imageUrl") or "").strip() or enrich.get("image_url"),
        "quantity_description": str(it.get("quantityDescription") or "").strip()
        or enrich.get("quantity_description"),
        "in_stock": it.get("isInStockAndAvailable", True),
    }
    return row, offer_inr, qty


def _rupees_to_paise(value: Any) -> int | None:
    """Parse a rupee string like '₹798' or '₹790.00' into paise."""
    if not isinstance(value, str):
        return None
    m = _PRICE_RE.search(value)
    if not m:
        return None
    try:
        return int(round(float(m.group()) * 100))
    except ValueError:
        return None


def _grand_total_paise(data: dict[str, Any], computed: int, has_priced: bool) -> int | None:
    # Live cart: string totals under cartTotalAmount / billBreakdown.toPay.value.
    paise = _rupees_to_paise(data.get("cartTotalAmount"))
    if paise is not None:
        return paise
    bill = data.get("billBreakdown")
    if isinstance(bill, dict):
        to_pay = bill.get("toPay")
        if isinstance(to_pay, dict):
            paise = _rupees_to_paise(to_pay.get("value"))
            if paise is not None:
                return paise
    # Legacy numeric fields.
    for src in (data, data.get("bill") if isinstance(data.get("bill"), dict) else None):
        if not isinstance(src, dict):
            continue
        for k in ("grandTotalPaise", "grand_total_paise", "billTotalPaise", "totalPaise"):
            if isinstance(src.get(k), int | float) and not isinstance(src.get(k), bool):
                return int(src[k])
        for k in ("grandTotal", "billTotal", "total", "subtotal"):
            if isinstance(src.get(k), int | float) and not isinstance(src.get(k), bool):
                return int(round(float(src[k]) * 100))
    return computed if has_priced else None


def _bill_breakdown(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Itemized bill lines (Item Total, Handling Fee, …) from billBreakdown.

    Each row: {label, value (display string), amount_paise (None for FREE),
    is_free}. The order Swiggy sends is preserved; the final To-Pay line is NOT
    included here (it's carried separately as grand_total_paise)."""
    bill = data.get("billBreakdown")
    if not isinstance(bill, dict):
        return None
    raw = bill.get("lineItems")
    if not isinstance(raw, list) or not raw:
        return None
    rows: list[dict[str, Any]] = []
    for li in raw:
        if not isinstance(li, dict):
            continue
        label = str(li.get("label") or "").strip()
        value = str(li.get("value") or "").strip()
        if not label:
            continue
        is_free = value.upper() == "FREE"
        rows.append(
            {
                "label": label,
                "value": value or "—",
                "amount_paise": None if is_free else _rupees_to_paise(value),
                "is_free": is_free,
            }
        )
    return rows or None


def cart_snapshot(
    envelope: dict[str, Any], *, spin_index: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    spin_index = spin_index or {}
    items: list[dict[str, Any]] = []
    computed, has_priced = 0, False
    for it in _items_raw(data):
        parsed = _cart_item(it, spin_index)
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
        "bill_breakdown": _bill_breakdown(data),
        "selected_address_id": selected_address_id,
    }


def build_cart_cards(
    results: list[ToolResult], *, session_products: Any = None
) -> list[Attachment]:
    spin_index = build_spin_index(session_products, results)
    # update_cart REPLACES the whole cart, so multiple cart calls in one turn
    # (e.g. update_cart then get_cart) describe the same final cart — render only
    # the last successful one to avoid duplicate cards.
    last: tuple[str, dict[str, Any]] | None = None
    for tr in results:
        if tr.tool not in _CART_TOOLS or not tr.ok:
            continue
        snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {}, spin_index=spin_index)
        if snap and snap.get("items"):
            last = (tr.tool, snap)
    if last is None:
        return []
    tool, snap = last
    return [
        Attachment(
            kind="instamart_cart_card",
            provider="swiggy_instamart",
            source=f"swiggy_instamart.{tool}",
            instamart_cart=snap,
        )
    ]
