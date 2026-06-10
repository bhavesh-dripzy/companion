"""Swiggy Food narrator digests — lean, COMPLETE renderings of big envelopes.

Same purpose as the Zomato digest: under raw-JSON truncation the narrator only
saw fragments of the first search hit and reported the rest as missing. Every
restaurant and priced menu row is rendered instead.
"""

from __future__ import annotations

from typing import Any

from core.providers.plugins.swiggy_food.cards import restaurant_entries
from core.providers.plugins.swiggy_food.menu_cards import menu_entries

_SEARCH_TOOLS = {"search_restaurants"}
_MENU_TOOLS = {"search_menu", "get_restaurant_menu"}
_MAX_CHARS = 2400
_MAX_MENU_ROWS = 30


def _restaurant_line(entry: dict[str, Any]) -> str:
    bits = [
        b
        for b in (
            f"rating {entry.get('rating')}" if entry.get("rating") else None,
            str(entry.get("eta") or "").strip() or None,
            str(entry.get("res_offer") or "").strip() or None,
        )
        if b
    ]
    tail = f" ({', '.join(bits)})" if bits else ""
    return f"- {entry['name']} [res_id={entry['res_id']}]{tail}"


def summarize(tool: str, envelope: dict[str, Any]) -> str | None:
    """Digest one Swiggy envelope for the narrator; None = use the raw fallback."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    if tool in _SEARCH_TOOLS:
        entries = restaurant_entries(envelope)
        if not entries:
            return None
        return (
            "Restaurants found (every result):\n" + "\n".join(_restaurant_line(e) for e in entries)
        )[:_MAX_CHARS]
    if tool in _MENU_TOOLS:
        rows = menu_entries(envelope)
        if not rows:
            return None
        lines = []
        for row in rows[:_MAX_MENU_ROWS]:
            name = str(row.get("item_name") or "").strip()
            if not name:
                continue
            price = row.get("discounted_price") or row.get("price")
            lines.append(f"- {name}" + (f" ₹{price}" if price not in (None, "", 0) else ""))
        extra = f"\n(+{len(rows) - _MAX_MENU_ROWS} more rows)" if len(rows) > _MAX_MENU_ROWS else ""
        return (f"Menu items returned ({len(rows)}):\n" + "\n".join(lines) + extra)[:_MAX_CHARS]
    return None
