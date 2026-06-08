"""Zomato menu-card builders (clean port of the legacy menu collector).

Ported from `agents.pipeline._collect_menu_attachments` / `_build_menu_card` /
`_derive_veg_kind`, adapted to operate on `ToolResult`s. Validated against a real
captured fixture (tests/core/fixtures/zomato/menu_listing.json).

Zomato nests the payload at `data.result.data`. Rich card rows live in
`menu_items[]` (by-categories tool) or `partial_menu[]` (listing tool); the thin
`item_mappings[]` is the *visible* slice. We render the rich rows whose variant_id
is visible, in the visible order, so the cards, the LLM's narration, and
`session_menu` always agree.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_MENU_CARD_TOOLS = {"get_restaurant_menu_by_categories", "get_menu_items_listing"}
_MAX_MENU_CARDS = 25
_DESC_MAX = 140


def _derive_veg_kind(item_tags: Any) -> str | None:
    """Map Zomato's `item_tags` onto a card veg flag (veg / non_veg / egg)."""
    if not isinstance(item_tags, list):
        return None
    norm = {str(t).strip().lower() for t in item_tags if isinstance(t, str)}
    if "non-veg" in norm or "nonveg" in norm:
        return "non_veg"
    if "egg" in norm or "egg-veg" in norm:
        return "egg"
    if "veg" in norm:
        return "veg"
    return None


def _menu_card(item: Any) -> dict[str, Any] | None:
    """Normalize one rich menu row into a card (name + item_id|variant_id required)."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    item_id = item.get("item_id")
    variant_id = item.get("variant_id")
    has_item = isinstance(item_id, str) and item_id.strip()
    has_variant = isinstance(variant_id, str) and variant_id.strip()
    if not has_item and not has_variant:
        return None
    card: dict[str, Any] = {"name": name.strip()}
    if has_item:
        card["item_id"] = item_id.strip()
    if has_variant:
        card["variant_id"] = variant_id.strip()
    price = item.get("price")
    if isinstance(price, int | float) and price > 0:
        card["price"] = price
    disc = item.get("discounted_price")
    if isinstance(disc, int | float) and disc > 0 and disc != price:
        card["discounted_price"] = disc
    image = item.get("image_url")
    if isinstance(image, str) and image.strip().startswith(("http://", "https://")):
        card["image_url"] = image.strip()
    rating = item.get("rating")
    if isinstance(rating, int | float) and rating > 0:
        card["rating"] = round(float(rating), 1)
    votes = item.get("reviews") or item.get("votes")
    if isinstance(votes, int | float) and votes > 0:
        card["votes"] = int(votes)
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        desc = description.strip()
        card["description"] = (desc[:_DESC_MAX] + "…") if len(desc) > _DESC_MAX else desc
    veg_kind = _derive_veg_kind(item.get("item_tags"))
    if veg_kind:
        card["veg_kind"] = veg_kind
    cats = item.get("categories")
    if isinstance(cats, list):
        clean = [c.strip() for c in cats if isinstance(c, str) and c.strip()]
        if clean:
            card["category"] = clean[0]
    return card


def _inner(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Walk the `data.result.data` nesting Zomato wraps the menu payload in."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    result = (
        data.get("result")
        if isinstance(data, dict) and isinstance(data.get("result"), dict)
        else None
    )
    inner = (
        result.get("data")
        if isinstance(result, dict) and isinstance(result.get("data"), dict)
        else None
    )
    return inner or None


def _visible_order(inner: dict[str, Any], rich_rows: list[Any]) -> list[str]:
    source = (
        inner.get("item_mappings") if isinstance(inner.get("item_mappings"), list) else rich_rows
    )
    order: list[str] = []
    seen: set[str] = set()
    for row in source:
        vid = row.get("variant_id") if isinstance(row, dict) else None
        if isinstance(vid, str) and vid.strip() and vid.strip() not in seen:
            seen.add(vid.strip())
            order.append(vid.strip())
    return order


def _menu_attachment(tr: ToolResult) -> Attachment | None:
    inner = _inner(tr.data if isinstance(tr.data, dict) else {})
    if not inner:
        return None
    rich_rows = inner.get("menu_items")
    if not isinstance(rich_rows, list) or not rich_rows:
        rich_rows = inner.get("partial_menu")
    if not isinstance(rich_rows, list) or not rich_rows:
        return None
    rich_by_vid: dict[str, dict[str, Any]] = {}
    for it in rich_rows:
        vid = it.get("variant_id") if isinstance(it, dict) else None
        if isinstance(vid, str) and vid.strip():
            rich_by_vid.setdefault(vid.strip(), it)
    cards: list[dict[str, Any]] = []
    for vid in _visible_order(inner, rich_rows):
        if len(cards) >= _MAX_MENU_CARDS:
            break
        card = _menu_card(rich_by_vid.get(vid))
        if card is not None:
            cards.append(card)
    if not cards:
        return None
    payload: dict[str, Any] = {
        "kind": "menu_cards",
        "source": f"zomato.{tr.tool}",
        "provider": "zomato",
        "items": cards,
    }
    res_id = inner.get("res_id") or (tr.arguments or {}).get("res_id")
    if res_id not in (None, ""):
        payload["res_id"] = res_id
    rname = inner.get("restaurant_name")
    if isinstance(rname, str) and rname.strip():
        payload["restaurant_name"] = rname.strip()
    cats = (tr.arguments or {}).get("categories")
    if isinstance(cats, list):
        clean = [c.strip() for c in cats if isinstance(c, str) and c.strip()]
        if clean:
            payload["categories"] = clean
    return Attachment(**payload)


def build_menu_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _MENU_CARD_TOOLS or not tr.ok:
            continue
        att = _menu_attachment(tr)
        if att is not None:
            out.append(att)
    return out
