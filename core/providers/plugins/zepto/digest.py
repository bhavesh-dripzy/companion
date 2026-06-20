"""Zepto narrator digest — the rupee-correct summary fed to the response LLM.

The generic `lean_digest` renders the RAW envelope, whose Zepto prices are in
paise (₹26 -> price:2600); the narrator then quotes "₹2,600" while the cards
(which normalize paise->rupees) show the right ₹26. This builds the digest from
the same normalized rows the cards use, so the narrator's text matches the cards.
Returns None for non-search/non-cart tools so the base lean digest still covers
order history, payment methods, addresses, etc.
"""

from __future__ import annotations

from typing import Any

from core.providers.plugins.zepto.cards import products_from_search
from core.providers.plugins.zepto.cart_cards import cart_snapshot

_SEARCH_TOOLS = {"search_products", "search_multiple_products", "get_past_order_items"}
_CART_TOOLS = {"view_cart", "update_cart"}
_MAX_ROWS = 12


def _price(row: dict[str, Any]) -> str:
    text = row.get("price_text")
    if text:
        return str(text)
    inr = row.get("price_inr")
    return f"₹{inr:g}" if isinstance(inr, int | float) else "?"


def _product_lines(envelope: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in products_from_search(envelope, query=None)[:_MAX_ROWS]:
        name = row.get("name")
        if not name:
            continue
        pack = f", {row['pack_size']}" if row.get("pack_size") else ""
        lines.append(f"  - {name} — {_price(row)}{pack}")
    return lines


def _cart_lines(envelope: dict[str, Any]) -> list[str]:
    snap = cart_snapshot(envelope)
    if snap is None:
        return ["Cart is empty."] if _is_empty(envelope) else []
    lines: list[str] = []
    for it in (snap.get("items") or [])[:_MAX_ROWS]:
        name = it.get("name") or "(item)"
        lines.append(f"  - {name} ×{it.get('quantity')} — {_price(it)}")  # noqa: RUF001
    total = snap.get("grand_total_paise")
    if isinstance(total, int | float):
        lines.append(f"  Cart total: ₹{round(total / 100):g}")
    return lines


def _is_empty(envelope: dict[str, Any]) -> bool:
    data = envelope.get("data")
    if isinstance(data, dict):
        items = data.get("items", data.get("cartItems"))
        return data.get("isEmpty") is True or (isinstance(items, list) and not items)
    return isinstance(data, str) and "cart is empty" in data.lower()


def summarize(tool: str, envelope: Any) -> str | None:
    """Rupee-correct digest for a search/cart envelope, else None."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    if tool in _SEARCH_TOOLS:
        lines = _product_lines(envelope)
        return "Products found:\n" + "\n".join(lines) if lines else None
    if tool in _CART_TOOLS:
        lines = _cart_lines(envelope)
        return "Cart:\n" + "\n".join(lines) if lines else None
    return None
