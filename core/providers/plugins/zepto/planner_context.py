"""Zepto session-context renderer for the clean planner stage.

`plan._generic_plan` calls `build_context(ctx)` and prepends the returned string
to the planner input. The transcript, current message, and resolved-address line
are added by `plan._format_input` — this renderer emits ONLY the session-state
BRIEF blocks (carried-forward placeholders, products shown, saved addresses,
current cart). It is a faithful parity port of the Zepto blocks in the legacy
`app/services/agents/pipeline.py::_format_routing_message` (headers, caps, and row
shapes are tuned and reproduced verbatim).

Reads from `ctx.session`:
  * `legacy["session_zepto_products"]` — list[dict] of shown products (LRU).
  * `legacy["session_zepto_addresses"]` — list[dict] of saved addresses.
  * `legacy["session_zepto_cart"]` — dict cart snapshot (items / is_empty).
  * `placeholders` — dict[str, str] of carried-forward opaque ids.

Stdlib + typing only; defensive (skips non-dict rows / missing ids); returns the
present blocks joined by blank lines, and "" when none apply.
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.planner_context import placeholders_block


def _products_block(products: Any) -> str | None:
    """Most-recently-shown Zepto products with their pvid + spid ids."""
    if not isinstance(products, list) or not products:
        return None
    product_rows: list[str] = []
    for entry in products[:12]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        pvid = str(entry.get("product_variant_id") or "").strip()
        spid = str(entry.get("store_product_id") or "").strip()
        if not name or not pvid or not spid:
            continue
        price = entry.get("price_text") or (
            f"₹{entry.get('price_inr')}" if entry.get("price_inr") else None
        )
        pack = entry.get("pack_size")
        tail_bits: list[str] = []
        if pack:
            tail_bits.append(str(pack))
        if price:
            tail_bits.append(str(price))
        tail = f" — {' / '.join(tail_bits)}" if tail_bits else ""
        product_rows.append(f"  - {name}{tail} → pvid={pvid}, spid={spid}")
    if not product_rows:
        return None
    return (
        "Zepto products already shown to the user this session — "
        "when the user picks one by name (or asks to add it / "
        "remove it), copy the literal pvid + spid below into "
        "`update_cart.cartItems[]` instead of re-running "
        "`search_products` (a fresh search returns a different "
        "pvid for the same product, which silently breaks the "
        "cart flow):\n" + "\n".join(product_rows)
    )


def _addresses_block(addresses: Any) -> str | None:
    """Saved delivery addresses available this session."""
    if not isinstance(addresses, list) or not addresses:
        return None
    zaddr_rows: list[str] = []
    for entry in addresses[:8]:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("address_id") or "").strip()
        label = str(entry.get("label") or entry.get("tag") or "").strip()
        if not aid or not label:
            continue
        line = str(entry.get("line") or entry.get("address_line") or "").strip()
        tail = f" ({line[:80]})" if line else ""
        zaddr_rows.append(f"  - {label} → addressId={aid}{tail}")
    if not zaddr_rows:
        return None
    return (
        "Zepto saved addresses available this session — when the "
        "user asks to switch delivery address, copy the literal "
        "addressId below into `select_saved_address`:\n" + "\n".join(zaddr_rows)
    )


def _cart_block(cart: Any) -> str | None:
    """Current Zepto cart snapshot (items with pvid/spid, or empty marker)."""
    if not isinstance(cart, dict):
        return None
    items = cart.get("items") or []
    if isinstance(items, list) and items:
        cart_lines: list[str] = []
        for it in items[:10]:
            if not isinstance(it, dict):
                continue
            nm = str(it.get("name") or "").strip() or "(item)"
            qty = it.get("quantity") or 1
            pvid = str(it.get("product_variant_id") or "").strip()
            spid = str(it.get("store_product_id") or "").strip()
            if not pvid or not spid:
                continue
            cart_lines.append(f"  - {nm} ×{qty} → pvid={pvid}, spid={spid}")  # noqa: RUF001
        if cart_lines:
            return (
                "Current Zepto cart (echoes server state from the "
                "last `view_cart` or optimistic projection from "
                "`update_cart`) — set `quantity: 0` for these "
                "pvid/spid pairs to remove items, or any positive "
                "number to set the new quantity:\n" + "\n".join(cart_lines)
            )
        return None
    if cart.get("is_empty"):
        return "Current Zepto cart: empty."
    return None


def build_context(ctx: Any) -> str:
    """Render the Zepto session-state brief blocks present in `ctx.session`.

    Returns the present blocks joined by blank lines, or "" when none apply.
    """
    # ExecCtx.session is the flat legacy dict already — read it directly.
    # (The old `session.legacy` path was wrong: getattr on a dict returns None.)
    legacy = getattr(ctx, "session", None)
    legacy = legacy if isinstance(legacy, dict) else {}
    placeholders = getattr(ctx, "placeholders", None)

    blocks: list[str | None] = [
        placeholders_block(placeholders, "zepto"),
        _products_block(legacy.get("session_zepto_products")),
        _addresses_block(legacy.get("session_zepto_addresses")),
        _cart_block(legacy.get("session_zepto_cart")),
    ]
    return "\n\n".join(block for block in blocks if block)
