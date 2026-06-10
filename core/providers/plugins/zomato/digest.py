"""Zomato narrator digests — lean, COMPLETE renderings of big envelopes.

A single keyword-search row is ~5KB (image URLs, deeplink params), so the
narrator's raw-JSON truncation used to show a fraction of the first-ranked
restaurant: it would describe that one and call everything else "not found"
(the Pizza Hut ₹169 Margherita reported missing while sitting in the same
envelope). These digests render every restaurant and priced item instead.
"""

from __future__ import annotations

from typing import Any

from core.providers.plugins.zomato.menu_index import menu_entries

_SEARCH_TOOLS = {"get_restaurants_for_keyword"}
_MENU_TOOLS = {"get_menu_items_listing", "get_restaurant_menu_by_categories"}
_MAX_CHARS = 2400
_ITEMS_PER_RESTAURANT = 5
_MAX_MENU_ROWS = 30


def _search_results(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    results = data.get("results")
    if not isinstance(results, list):
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        results = inner.get("results") if isinstance(inner.get("results"), list) else []
    return [r for r in results if isinstance(r, dict)]


def _price(row: dict[str, Any]) -> str:
    value = row.get("discounted_price") or row.get("price") or row.get("min_price")
    return f" ₹{value}" if value not in (None, "", 0) else ""


def _restaurant_line(resto: dict[str, Any]) -> str | None:
    name = str(resto.get("name") or "").strip()
    rid = str(resto.get("res_id") or "").strip()
    if not name:
        return None
    bits = [
        b
        for b in (
            f"rating {resto.get('rating')}" if resto.get("rating") else None,
            str(resto.get("eta") or "").strip() or None,
            str(resto.get("res_offer") or "").strip() or None,
        )
        if b
    ]
    items = resto.get("items") if isinstance(resto.get("items"), list) else []
    previews = []
    for it in items[:_ITEMS_PER_RESTAURANT]:
        if not isinstance(it, dict):
            continue
        iname = str(it.get("name") or "").strip()
        if iname:
            previews.append(f"{iname}{_price(it)}")
    head = (
        f"- {name}"
        + (f" [res_id={rid}]" if rid else "")
        + (f" ({', '.join(bits)})" if bits else "")
    )
    return head + (f": {'; '.join(previews)}" if previews else "")


def summarize(tool: str, envelope: dict[str, Any]) -> str | None:
    """Digest one Zomato envelope for the narrator; None = use the raw fallback."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    if tool in _SEARCH_TOOLS:
        lines = [ln for r in _search_results(envelope) if (ln := _restaurant_line(r))]
        if not lines:
            return None
        return ("Restaurants found (every result):\n" + "\n".join(lines))[:_MAX_CHARS]
    if tool in _MENU_TOOLS:
        rows = menu_entries(envelope)
        if not rows:
            return None
        lines = [
            f"- {row['item_name']}{_price(row)}"
            for row in rows[:_MAX_MENU_ROWS]
            if row.get("item_name")
        ]
        extra = f"\n(+{len(rows) - _MAX_MENU_ROWS} more rows)" if len(rows) > _MAX_MENU_ROWS else ""
        return (f"Menu items returned ({len(rows)}):\n" + "\n".join(lines) + extra)[:_MAX_CHARS]
    return None
