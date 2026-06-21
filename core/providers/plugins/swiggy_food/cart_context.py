"""Swiggy Food cart block for the planner brief (mirrors Instamart's `_cart_block`).

`update_food_cart` is an UPSERT in the executor (it folds onto the cart's existing
lines), so the planner only needs to list the item it's adding or changing — this
block tells it so, and shows the current cart contents keyed by menu_item_id.
"""

from __future__ import annotations

from typing import Any


def cart_block(session_cart: Any) -> str | None:
    if not isinstance(session_cart, dict):
        return None
    items = session_cart.get("items") or []
    rows: list[str] = []
    for it in items[:10] if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("item_name") or it.get("name") or "").strip() or "(item)"
        iid = str(it.get("menu_item_id") or it.get("variant_id") or "").strip()
        if not iid:
            continue
        qty = it.get("quantity") or 1
        rows.append(f"  - {name} ×{qty} → menu_item_id={iid}")  # noqa: RUF001 (legacy row shape)
    if not rows:
        if session_cart.get("is_empty"):
            return "Current Swiggy cart: empty."
        return None
    rname = str(session_cart.get("restaurant_name") or "").strip()
    head = f"Current Swiggy cart ({rname})" if rname else "Current Swiggy cart"
    return (
        head + " — `update_food_cart` UPSERTS (the executor preserves these lines), "
        "so list ONLY the menu_item_id you're adding or changing (quantity = new "
        "absolute qty); to remove a line send it with quantity 0; adding an item "
        "from a DIFFERENT restaurant replaces the whole cart:\n" + "\n".join(rows)
    )
