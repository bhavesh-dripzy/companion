"""Pure helpers for GroceryDomainHandler — kept out of grocery.py for the file-size budget.

Stateless free functions for two concerns, all provider-agnostic (they operate on the normalized
Cart contract, so they apply to every grocery provider uniformly):
- Deterministic resolution safety-net (`fallback`/`name_candidates`) when the LLM Resolver punts.
- Rendering: normalized cards, legacy FE attachments, selected-address keys, disambiguation options.
Search-result helpers (filtering, `shown_for`, `product_card`) live in grocery_search.py and are
re-exported below so callers/tests keep importing them as `grocery_view.*`.
"""

from __future__ import annotations

from typing import Any

from core.agent.contracts import Cart, fmt_minor
from core.agent.domains.grocery_search import (  # re-export (search helpers split out for budget)
    apply_filter,
    filter_from_intent,
    filter_label,
    product_card,
    shown_for,
)
from core.agent.legacy_cards import cart_attachment

__all__ = ["apply_filter", "filter_from_intent", "filter_label", "product_card", "shown_for"]

DOMAIN = "grocery"


# ---------------- legacy-attachment + address helpers ----------------
# Address is PER-PROVIDER: the source of truth is `provider_session[pid]` (the same place exec_ctx
# reads for the real MCP calls). Never the per-domain `selected_context` — that holds one address
# and would leak across providers (a Zepto switch poisoning Instamart's address pointer).
def provider_addr(state, pid: str) -> str | None:
    return (state.provider_session.get(pid) or {}).get("address_id")


def addr_keys(state, chosen: list[str]) -> dict[str, Any]:
    # Each provider's OWN address id (so the FE round-trips them independently, never crossed).
    return {f"selected_{pid}_address_id": provider_addr(state, pid) for pid in chosen}


def cart_atts(pid, cart: Cart, state) -> list[dict[str, Any]]:
    att = cart_attachment(pid, cart, provider_addr(state, pid))
    return [att] if att else []


def enrich(cart: Cart, state) -> Cart:
    """Zepto carts omit name/price → fill from the catalog (price-enrichment, §5b)."""
    by_id = {p.item_id: p for _r, p in state.catalog.entries.items()}
    total = 0
    for it in cart.items:
        src = by_id.get(it.item_id)
        if src:
            it.name = it.name or src.name
            it.price_minor = it.price_minor or src.price_minor
            it.image_url = it.image_url or src.image_url
        total += it.price_minor * it.qty
    if not cart.total_minor:
        cart.total_minor = total
    return cart


def free_delivery(pid: str, cart: Cart) -> dict[str, Any] | None:
    """How far the cart is from the provider's free-delivery minimum (source of truth: ₹99 on both
    Instamart & Zepto). The narrator uses `gap` to nudge precisely. None when the provider has no
    threshold or the cart is empty."""
    from core.agent.registry import REGISTRY

    threshold = getattr(REGISTRY.provider(pid), "free_delivery_minor", 0)
    if not threshold or cart.is_empty:
        return None
    item_total = sum(i.price_minor * i.qty for i in cart.items)
    gap = max(0, threshold - item_total)
    return {
        "min": fmt_minor(threshold),
        "item_total": fmt_minor(item_total),
        "gap": fmt_minor(gap),
        "qualifies": gap == 0,
    }


def cart_card(pid, cart: Cart) -> dict[str, Any]:
    return {
        "type": "cart",
        "provider": pid,
        "empty": cart.is_empty,
        "items": [
            {"item_id": i.item_id, "name": i.name, "qty": i.qty, "price": fmt_minor(i.price_minor)}
            for i in cart.items
        ],
        "total": fmt_minor(cart.total_minor),
        # Fee breakdown (Instamart provides it; Zepto via get_payment_methods) → narrator analyzes.
        "bill": [
            {"label": b.get("label"), "value": fmt_minor(b.get("minor", 0))}
            for b in (cart.bill or [])
        ],
        # Exact distance to free delivery (₹99 min) → lets the narrator say "add ₹X more".
        "free_delivery": free_delivery(pid, cart),
        # Set when checkout pricing failed (store closed / not serviceable) → prompt address change.
        "unavailable": cart.unavailable or None,
    }


def header(state, pid: str | None = None) -> dict[str, Any]:
    # The delivering-to label for a single provider's card — read its own provider_session, not the
    # shared selected_context. None (e.g. a fan-out) → no single address to show.
    ps = (state.provider_session.get(pid) or {}) if pid else {}
    return {
        "label": ps.get("label") or ps.get("address_id") or "",
        "change_action": "open_address_picker",
    }


def _register_cart_items(state, pid: str, cart: Cart) -> None:
    """Make every LIVE cart line catalog-addressable. The real provider cart persists across sessions
    but the session catalog does not, so a line added in a past session is otherwise invisible to
    set_qty/remove resolution (it isn't in the catalog → not in the candidate set → 'not found').
    Registering the cart's own items (they carry name/id/secondary_id) fixes both the candidate set
    and the write-path `by_ref` lookup."""
    from core.agent.contracts import Product

    known = {p.item_id for _r, p in state.catalog.entries.items() if p.provider == pid}
    new = [
        Product(
            provider=pid,
            item_id=i.item_id,
            name=i.name,
            price_minor=i.price_minor,
            secondary_id=i.secondary_id,
            image_url=i.image_url,
        )
        for i in cart.items
        if i.item_id and i.name and i.item_id not in known
    ]
    if new:
        state.catalog.append(new)


def build_delta(state, resolved: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, dict]]:
    """Resolved refs → (delta, meta) for the cart write. An NL-resolved ref reads its identity from
    the catalog product; a DETERMINISTIC TAP carries its own identity (item_id/secondary_id/name),
    so 'add this exact card' works even when the item isn't in the session catalog (restart/cold
    start) — no LLM, no re-search, no wrong variant."""
    delta: dict[str, int] = {}
    meta: dict[str, dict] = {}
    for r in resolved:
        prod = state.catalog.by_ref(r.get("ref"))
        if prod:
            iid, sid, name, price = prod.item_id, prod.secondary_id, prod.name, prod.price_minor
        elif r.get("item_id"):
            iid = r["item_id"]
            sid, name, price = r.get("secondary_id") or "", r.get("name") or iid, 0
        else:
            continue
        delta[iid] = int(r.get("qty", 1))
        meta[iid] = {"name": name, "secondary_id": sid, "price_minor": price}
    return delta, meta


def refresh_cart(state, pid: str, read) -> None:
    """Server is the source of truth — overwrite the in-memory cache with the live cart (enriched
    from the catalog). No-op on a failed read so we never wipe the cache on a transient error."""
    if read.ok and read.cart is not None:
        state.carts[pid] = enrich(read.cart, state)
        _register_cart_items(state, pid, state.carts[pid])


async def read_into_cache(state, pid: str) -> None:
    """Open a session and refresh the cache from the live provider cart (for show_cart/checkout)."""
    import contextlib

    from core.agent.context import CONTEXT
    from core.agent.registry import REGISTRY

    adapter = REGISTRY.provider(pid)
    with contextlib.suppress(Exception):
        async with adapter.open_session(state.user_id) as s:
            ctx = await CONTEXT.exec_ctx(adapter, s, state)
            await adapter.ensure_context(s, ctx)
            refresh_cart(state, pid, await adapter.read_cart(s, ctx))
            await adapter.price_cart(s, ctx, state.cart(pid))  # final total / store-closed flag


def cart_result(state, pid: str, cart: Cart, *, status="ok", error=None, **extra) -> dict[str, Any]:
    """The standard cart-shaped turn result (card + legacy attachment + header). `extra` adds
    note/message/detail/selected_*_address_id without repeating the boilerplate at every call site."""
    out = {
        "domain": DOMAIN,
        "status": status,
        "error": error,
        "cards": [cart_card(pid, cart)],
        "attachments": cart_atts(pid, cart, state),
        "context_header": header(state, pid),
    }
    out.update(extra)
    return out


def disambiguation_result(state, pid, added, options, unresolved) -> dict[str, Any]:
    """A turn where SOME items resolved (already added to the cart) and SOME are ambiguous/unknown.
    We show the cart with the added items, surface the choices as a `disambiguate` pending-action
    (the FE renders them as tappable chips), and hand the narrator a precise note of the scenario —
    so partial adds never silently drop the items that DID resolve. Provider-agnostic."""
    parts = []
    if added:
        parts.append(f"{', '.join(added)} cart mein add kar diya.")
    for o in options:
        names = [c["name"] for c in o.get("choices", []) if c.get("name")]
        if names:
            parts.append(f"'{o['phrase']}' ke liye kaunsa — {' ya '.join(names)}?")
    if unresolved:
        parts.append(f"'{', '.join(unresolved)}' nahi mila.")
    cart = state.cart(pid)
    return {
        "domain": DOMAIN,
        "status": "ambiguous",
        "error": None,
        "cards": [cart_card(pid, cart)] if added else [],
        "attachments": cart_atts(pid, cart, state) if added else [],
        "pending_action": {"type": "disambiguate", "options": options},
        "note": " ".join(parts),
        "context_header": header(state, pid),
        **addr_keys(state, [pid]),
    }


def find_note(added: list[str], phrases: list[str]) -> str:
    """Narrator note for a FIND turn: confirm anything that got added, then frame the freshly-searched
    items as a pick ('ye rahe — kaunsa?'), so the narrator presents discovery, not a status report."""
    pre = f"{', '.join(added)} cart mein add kar diya. " if added else ""
    return f"{pre}{', '.join(phrases)} ye rahe — kaunsa add karu?"


def not_found(state, pid, note: str) -> dict[str, Any]:
    """A set_qty/remove that targeted something not in the cart — shows the live cart + a clear note
    (never a fake success, never an empty disambiguation card)."""
    return cart_result(
        state,
        pid,
        state.cart(pid),
        status="not_found",
        error="STALE_ITEM",
        note=note,
        **addr_keys(state, [pid]),
    )
