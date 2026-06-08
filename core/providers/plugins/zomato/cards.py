"""Zomato restaurant-card builders (clean port of the legacy collectors).

Ported from `agents.pipeline._build_restaurant_card_*` / `_collect_restaurant_attachments`,
adapted to operate on `ToolResult`s. Validated against a real captured fixture
(tests/core/fixtures/zomato/search_pizza.json).
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_RESTAURANT_CARD_TOOLS = {"get_restaurants_for_keyword"}
_MAX_FEATURED_ITEMS = 3
_MAX_RESTAURANT_CARDS = 8

_ENTRY_FIELDS = (
    ("rating", "rating"),
    ("votes", "votes"),
    ("eta", "eta"),
    ("distance", "distance_km"),
    ("res_image", "res_image"),
    ("res_offer", "res_offer"),
    ("serviceability_status", "serviceability_status"),
    ("chain_id", "chain_id"),
)


def _card_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    vid = item.get("variant_id")
    if not isinstance(name, str) or not name.strip() or not isinstance(vid, str) or not vid.strip():
        return None
    out: dict[str, Any] = {"name": name.strip(), "variant_id": vid.strip()}
    item_id = item.get("catalogue_id") or item.get("item_id")
    if isinstance(item_id, str) and item_id.strip():
        out["item_id"] = item_id.strip()
    price = item.get("price")
    if isinstance(price, int | float) and price > 0:
        out["price"] = price
    min_price = item.get("min_price")
    if isinstance(min_price, int | float) and min_price > 0 and min_price != price:
        out["min_price"] = min_price
    image = item.get("image_link") or item.get("image_url")
    if isinstance(image, str) and image.strip().startswith(("http://", "https://")):
        out["image_url"] = image.strip()
    if isinstance(item.get("is_veg"), bool):
        out["veg_kind"] = "veg" if item["is_veg"] else "non_veg"
    return out


def _card_entry(resto: Any) -> dict[str, Any] | None:
    if not isinstance(resto, dict):
        return None
    rid_raw = resto.get("res_id") or resto.get("restaurant_id")
    name = resto.get("name") or resto.get("restaurant_name")
    if rid_raw is None or not isinstance(name, str) or not name.strip():
        return None
    rid = str(rid_raw).strip()
    if not rid:
        return None
    out: dict[str, Any] = {"res_id": rid, "name": name.strip()}
    for src, dst in _ENTRY_FIELDS:
        value = resto.get(src)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                out[dst] = value.strip()
        else:
            out[dst] = value
    items: list[dict[str, Any]] = []
    for raw_item in resto.get("items") if isinstance(resto.get("items"), list) else []:
        card = _card_item(raw_item)
        if card:
            items.append(card)
        if len(items) >= _MAX_FEATURED_ITEMS:
            break
    if items:
        out["items"] = items
    return out


def _results_rows(envelope: dict[str, Any]) -> list[Any]:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("results"), list):
        return data["results"]
    inner = data.get("data")
    if isinstance(inner, dict) and isinstance(inner.get("results"), list):
        return inner["results"]
    return []


def restaurant_entries(result_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized restaurant card entries from one search envelope (deduped, capped)."""
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for resto in _results_rows(result_data):
        if len(cards) >= _MAX_RESTAURANT_CARDS:
            break
        entry = _card_entry(resto)
        if entry is None or entry["res_id"] in seen:
            continue
        seen.add(entry["res_id"])
        cards.append(entry)
    return cards


def build_restaurant_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _RESTAURANT_CARD_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        cards = restaurant_entries(envelope)
        if not cards:
            continue
        payload: dict[str, Any] = {
            "kind": "restaurant_cards",
            "source": f"zomato.{tr.tool}",
            "provider": "zomato",
            "restaurants": cards,
        }
        keyword = (tr.arguments or {}).get("keyword") or (tr.arguments or {}).get("query")
        if isinstance(keyword, str) and keyword.strip():
            payload["keyword"] = keyword.strip()
        out.append(Attachment(**payload))
    return out
