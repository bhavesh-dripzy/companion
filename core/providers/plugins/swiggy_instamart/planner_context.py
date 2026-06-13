"""Swiggy Instamart planner session-context renderer (clean port).

Faithful parity port of the Instamart session blocks from the legacy
``agents.pipeline._format_routing_message`` (the ``session_instamart_products``
/ ``session_instamart_addresses`` / ``session_instamart_cart`` branches), plus
the shared carried-forward placeholders line. Renders ONLY the session-state
brief prepended to the planner input — the transcript, current user message and
``(resolved address_id: ...)`` line are added by ``plan._format_input`` and must
NOT be repeated. Headers, caps, row shapes and literal-id guidance are verbatim
from legacy.

Reads typed ``ctx.session`` (``core/contracts/session.py``): the ``legacy`` flat
bag (``session_instamart_products`` / ``session_instamart_addresses`` /
``session_instamart_cart``), ``placeholders`` and ``selected_address_id``.
Stdlib + typing only; no ``app.services``/``core`` imports.
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.planner_context import placeholders_block


def _products_block(session_instamart_products: Any) -> str | None:
    if not isinstance(session_instamart_products, list) or not session_instamart_products:
        return None
    rows: list[str] = []
    for entry in session_instamart_products[:15]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        brand = str(entry.get("brand") or "").strip()
        header = f"  - {name}" + (f" ({brand})" if brand else "") + ":"
        variants = entry.get("variants")
        if not isinstance(variants, list) or not variants:
            continue
        var_lines: list[str] = []
        for var in variants:
            if not isinstance(var, dict):
                continue
            spin_id = str(var.get("spin_id") or "").strip()
            if not spin_id:
                continue
            qty = str(var.get("quantity_description") or "").strip() or "?"
            price = var.get("price_inr")
            price_str = (
                f" ₹{price:g}"
                if isinstance(price, int | float) and not isinstance(price, bool)
                else ""
            )
            var_lines.append(f"      {qty}{price_str} → spinId={spin_id}")
        if not var_lines:
            continue
        rows.append(header)
        rows.extend(var_lines)
    if not rows:
        return None
    return (
        "Instamart products already shown to the user this session — "
        "when the user picks one by name, copy the literal spinId below into "
        "`update_cart.items[]` instead of re-running `search_products`. "
        "Each product lists its variants (pack sizes) — if the user hasn't "
        "specified a variant, ask them to pick one before adding to cart:\n" + "\n".join(rows)
    )


def _addresses_block(
    session_instamart_addresses: Any, selected_address_id: str | None
) -> str | None:
    if not isinstance(session_instamart_addresses, list) or not session_instamart_addresses:
        return None
    sel = (selected_address_id or "").strip()
    addr_rows: list[str] = []
    for entry in session_instamart_addresses[:8]:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("address_id") or "").strip()
        if not aid:
            continue
        label = str(entry.get("label") or "").strip() or str(entry.get("category") or "").strip()
        line = str(entry.get("address_line") or "").strip()
        tail = f" ({line[:80]})" if line else ""
        head = label or "(unlabelled)"
        marker = "  * " if aid == sel else "  - "
        addr_rows.append(f"{marker}{head} → addressId={aid}{tail}")
    if not addr_rows:
        return None
    return (
        "Instamart saved addresses available this session — when the "
        "user asks to switch delivery address, copy the literal "
        "addressId below; the executor will substitute it into "
        "`__ADDRESS_ID__` automatically:\n" + "\n".join(addr_rows)
    )


def _cart_block(session_instamart_cart: Any) -> str | None:
    if not isinstance(session_instamart_cart, dict):
        return None
    items = session_instamart_cart.get("items") or []
    if isinstance(items, list) and items:
        cart_lines: list[str] = []
        for it in items[:10]:
            if not isinstance(it, dict):
                continue
            nm = str(it.get("name") or "").strip() or "(item)"
            qty = it.get("quantity") or 1
            spin = str(it.get("spin_id") or "").strip()
            if not spin:
                continue
            cart_lines.append(f"  - {nm} ×{qty} → spinId={spin}")  # noqa: RUF001 (legacy row shape)
        if cart_lines:
            total = session_instamart_cart.get("grand_total_paise")
            total_line = (
                f" (grand total: ₹{total / 100:.0f})"
                if isinstance(total, int | float) and not isinstance(total, bool) and total
                else ""
            )
            return (
                "Current Instamart cart (last `get_cart` snapshot)"
                + total_line
                + " — Instamart `update_cart` is REPLACE-ALL, so to add a "
                "new item or change qty you must re-list every spinId "
                "below with the desired quantity (omit a line, or use "
                "`clear_cart`, to remove):\n" + "\n".join(cart_lines)
            )
    if session_instamart_cart.get("is_empty"):
        return "Current Instamart cart: empty."
    return None


def build_context(ctx: Any) -> str:
    """Render the Instamart session brief prepended to the planner input.

    Emits, in order, only the blocks whose backing session state is present:
    carried-forward placeholders, the product LRU, saved addresses (selected row
    starred) and the cart snapshot. Blocks are joined by a blank line; returns
    ``""`` when nothing applies.
    """
    session = ctx.session
    legacy = session.legacy if isinstance(session.legacy, dict) else {}
    parts: list[str | None] = [
        placeholders_block(session.placeholders or {}, "swiggy_instamart"),
        _products_block(legacy.get("session_instamart_products")),
        _addresses_block(legacy.get("session_instamart_addresses"), session.selected_address_id),
        _cart_block(legacy.get("session_instamart_cart")),
    ]
    return "\n\n".join(block for block in parts if block)
