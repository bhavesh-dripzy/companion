"""Pure projection helpers for the grounded grocery flow (grocery_agent.py) — the read-only context
the two LLMs see and the FACTS the code records. No I/O, no MCP: just shapes over `state`."""

from __future__ import annotations

from typing import Any

from core.agent.domains import grocery_view as V


def _money(minor: int) -> float | int:
    """Paise → rupees, as an int when whole (so we say '₹232', not '₹232.0')."""
    return minor // 100 if minor % 100 == 0 else round(minor / 100, 2)


def _cart_compact(state, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The REAL cart per app for the LLMs — only what's needed to resolve references: each line's
    `ref`, name, qty and `new` flag (true = added this session). No totals here — code owns numbers."""
    out: dict[str, dict[str, Any]] = {}
    for pid in ids:
        c = state.cart(pid)
        V._register_cart_items(state, pid, c)  # make every live line ref-addressable
        added = state.session_added.get(pid, set())
        out[pid] = {
            "items": [
                {
                    "ref": state.catalog.ref_of(pid, i.item_id),
                    "name": i.name,
                    "qty": i.qty,
                    "new": i.item_id in added,
                }
                for i in c.items
            ]
        }
    return out


def _cart_facts(state, pid: str, items: bool = False) -> dict[str, Any]:
    """The authoritative cart numbers for an app, built by CODE — handed to the responder so the
    spoken count/total are always exactly the real cart (the responder never computes). When the
    store is closed / not serviceable the total is meaningless (₹0), so we OMIT it and flag
    `unavailable` instead — the responder then talks serviceability, not a fake ₹0 total."""
    c = state.cart(pid)
    f: dict[str, Any] = {"count": sum(i.qty for i in c.items)}
    if c.unavailable:
        f["unavailable"] = c.unavailable
    else:
        f["total"] = f"₹{_money(c.total_minor)}"
    if items:
        f["items"] = [f"{i.name} x{i.qty}" for i in c.items]
    return f


def _found(state, pid: str, query: str) -> list[dict[str, Any]]:
    """The shown products for (app, query), cheapest first — facts for the responder to present."""
    return [
        {"name": p.name, "brand": p.brand, "size": p.variation, "price": _money(p.price_minor)}
        for _, p in state.catalog.bucket(pid, query)[:8]
    ]


def _old_items(state, ids: list[str]) -> dict[str, list[str]]:
    """Leftover cart lines (names) from BEFORE this session — per app, only once the user has added
    something this session, and only until we've flagged that app (so it's mentioned exactly once)."""
    out: dict[str, list[str]] = {}
    for pid in ids:
        added = state.session_added.get(pid, set())
        if pid in state.old_items_flagged or not added:
            continue
        old = [i.name for i in state.cart(pid).items if i.item_id not in added]
        if old:
            out[pid] = old
    return out


def _resolve_selector(state, pid: str, query: str, select: str) -> str | None:
    """Pick a ref from the freshly-searched bucket for a selector-add: 'cheapest' (or empty) → the
    lowest price; any other hint → the cheapest product whose name/brand matches it. None if no match."""
    bucket = state.catalog.bucket(pid, query)  # (ref, product), cheapest first
    if not bucket:
        return None
    sel = (select or "cheapest").strip().lower()
    if sel in ("", "cheapest", "sasta", "saste", "sabse sasta"):
        return bucket[0][0]
    for ref, p in bucket:  # already cheapest-first → first match is the cheapest match
        hay = f"{p.name} {p.brand or ''}".lower()
        if sel in hay:
            return ref
    return None
