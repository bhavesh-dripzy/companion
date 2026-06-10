"""Swiggy menu-card builders (clean port of the legacy collectors).

Ported from `agents.pipeline._build_swiggy_menu_card` / `_iter_swiggy_menu_items`
/ `_collect_swiggy_menu_attachments`, adapted to operate on `ToolResult`s. Same
`menu_cards` shape as Zomato; reads Swiggy's `search_menu` (data.items[]) and
`get_restaurant_menu` (data.categories[].items[]) envelopes.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_MENU_CARD_TOOLS = {"search_menu", "get_restaurant_menu"}
_MAX_MENU_CARDS = 25
_DESC_MAX = 140
_IMG_PREFIX = "https://media-assets.swiggy.com/swiggy/image/upload/"


def _menu_card(item: Any) -> dict[str, Any] | None:
    """Normalize one Swiggy menu item (id + name required)."""
    if not isinstance(item, dict):
        return None
    iid_raw = (
        item.get("menu_item_id") or item.get("menuItemId") or item.get("id") or item.get("itemId")
    )
    name = item.get("name") or item.get("item_name")
    if iid_raw is None or not isinstance(name, str) or not name.strip():
        return None
    iid = str(iid_raw).strip()
    if not iid:
        return None
    # One id reused for both keys: the frontend reads variant_id then item_id.
    card: dict[str, Any] = {"name": name.strip(), "variant_id": iid, "item_id": iid}
    price = item.get("price") or item.get("finalPrice")
    if isinstance(price, int | float) and price > 0:
        card["price"] = price
    image = item.get("imageUrl") or item.get("image_url")
    if isinstance(image, str) and image.strip():
        url = image.strip()
        card["image_url"] = (
            url if url.startswith(("http://", "https://")) else f"{_IMG_PREFIX}{url}"
        )
    rating = item.get("rating")
    if isinstance(rating, str):
        try:
            rating_f = float(rating)
        except ValueError:
            rating_f = 0.0
        if rating_f > 0:
            card["rating"] = round(rating_f, 1)
    elif isinstance(rating, int | float) and rating > 0:
        card["rating"] = round(float(rating), 1)
    is_veg = item.get("isVeg")
    if isinstance(is_veg, bool):
        card["veg_kind"] = "veg" if is_veg else "non_veg"
    elif isinstance(is_veg, int):
        card["veg_kind"] = "veg" if is_veg == 1 else "non_veg"
    desc = item.get("description")
    if isinstance(desc, str) and desc.strip():
        d = desc.strip()
        card["description"] = (d[:_DESC_MAX] + "…") if len(d) > _DESC_MAX else d
    return card


def _iter_menu_items(envelope: dict[str, Any]):
    """Yield raw menu items from `data.items[]` (search) or `data.categories[].items[]`."""
    data = envelope.get("data")
    if not isinstance(data, dict):
        return
    items = data.get("items")
    if isinstance(items, list):
        yield from (it for it in items if isinstance(it, dict))
    cats = data.get("categories") or data.get("menu_categories")
    if isinstance(cats, list):
        for cat in cats:
            sub = cat.get("items") if isinstance(cat, dict) else None
            if isinstance(sub, list):
                yield from (it for it in sub if isinstance(it, dict))


def _menu_attachment(tr: ToolResult) -> Attachment | None:
    envelope = tr.data if isinstance(tr.data, dict) else {}
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in _iter_menu_items(envelope):
        if len(cards) >= _MAX_MENU_CARDS:
            break
        card = _menu_card(raw_item)
        if card is None:
            continue
        vid = card["variant_id"]
        if vid in seen:
            continue
        seen.add(vid)
        cards.append(card)
    if not cards:
        return None
    payload: dict[str, Any] = {
        "kind": "menu_cards",
        "source": f"swiggy_food.{tr.tool}",
        "provider": "swiggy",
        "items": cards,
    }
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    rest = data.get("restaurant") if isinstance(data.get("restaurant"), dict) else {}
    rname = rest.get("name") or rest.get("restaurantName")
    if isinstance(rname, str) and rname.strip():
        payload["restaurant_name"] = rname.strip()
    rid = rest.get("id") or rest.get("restaurant_id")
    if rid not in (None, ""):
        payload["res_id"] = rid
    return Attachment(**payload)


def _trim_variants_v2(raw: Any) -> list[dict[str, Any]]:
    """Lean copy of a response-shape variantsV2 block (groups/variations capped)
    — enough for the planner brief tail and the cart variant gate, without
    bloating the session round trip."""
    groups: list[dict[str, Any]] = []
    for group in raw if isinstance(raw, list) else []:
        if not isinstance(group, dict):
            continue
        variations: list[dict[str, Any]] = []
        for v in (group.get("variations") or [])[:6]:
            if not isinstance(v, dict):
                continue
            vid = v.get("id") or v.get("variantId") or v.get("variant_id")
            if vid in (None, ""):
                continue
            row: dict[str, Any] = {"id": str(vid), "name": str(v.get("name") or "")}
            if v.get("price") is not None:
                row["price"] = v.get("price")
            if v.get("inStock") is not None:
                row["inStock"] = v.get("inStock")
            if v.get("default") is not None:
                row["default"] = v.get("default")
            variations.append(row)
        if variations:
            groups.append(
                {
                    "groupId": str(group.get("groupId") or group.get("group_id") or ""),
                    "name": str(group.get("name") or ""),
                    "variations": variations,
                }
            )
        if len(groups) >= 4:
            break
    return groups


def menu_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Lean session_menu rows for the planner and the cart variant gate.

    Carries `menu_item_id` (so the brief labels the row as a Swiggy cart id)
    plus `hasVariants`/trimmed `variantsV2` — without these the brief renders
    Swiggy rows Zomato-style and a follow-up cart add cannot resolve the
    item's required size/crust choice."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    rest = data.get("restaurant") if isinstance(data.get("restaurant"), dict) else {}
    res_id = rest.get("id") or rest.get("restaurant_id")
    res_id = str(res_id).strip() if res_id not in (None, "") else None
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _iter_menu_items(envelope):
        card = _menu_card(raw)
        if card is None or card["variant_id"] in seen:
            continue
        seen.add(card["variant_id"])
        entry = {
            "variant_id": card["variant_id"],
            "menu_item_id": card["variant_id"],
            "item_name": card["name"],
            "price": card.get("price"),
        }
        if res_id:
            entry["res_id"] = res_id
        if raw.get("hasVariants"):
            entry["hasVariants"] = True
        trimmed = _trim_variants_v2(raw.get("variantsV2"))
        if trimmed:
            entry["variantsV2"] = trimmed
        out.append(entry)
    return out


def build_menu_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _MENU_CARD_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        att = _menu_attachment(tr)
        if att is not None:
            out.append(att)
    return out
