"""Saved-address label parsing for MCP provider responses (web/admin helper).

Relocated from `app/services/food_response_builder.py` in the legacy teardown.
Used by the MCP saved-addresses route to render a human label per address row;
different providers use different field casings (Swiggy camelCase vs Zomato
snake_case), so this normalizes across them.
"""

from __future__ import annotations

from typing import Any


def saved_address_label_from_mcp_item(item: dict[str, Any]) -> str:
    """Human-facing label for one saved-address row from MCP (Zomato, Swiggy, etc.).

    Swiggy Food ``get_addresses`` uses camelCase: ``addressLine``, ``addressTag``,
    ``addressCategory``, ``id`` — not ``location_name`` / ``address_line``.
    """
    if not isinstance(item, dict):
        return ""
    row_id = str(item.get("address_id") or item.get("addressId") or item.get("id") or "").strip()

    def ok(text: str) -> bool:
        t = (text or "").strip()
        return bool(t) and t != row_id

    tag = str(item.get("addressTag") or item.get("address_tag") or "").strip()
    category = str(item.get("addressCategory") or item.get("address_category") or "").strip()
    line = str(
        item.get("addressLine")
        or item.get("address_line")
        or item.get("formatted_address")
        or item.get("formattedAddress")
        or item.get("location_name")
        or item.get("name")
        or item.get("label")
        or ""
    ).strip()

    if tag and line:
        return f"{tag} — {line}" if ok(line) else tag
    if line and ok(line):
        return line
    if tag:
        return tag
    if category and line:
        return f"{category}: {line}"
    if category:
        return category
    for key in ("location_name", "name", "label"):
        v = str(item.get(key) or "").strip()
        if ok(v):
            return v
    return ""
