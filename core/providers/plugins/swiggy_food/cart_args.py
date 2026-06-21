"""Swiggy cart-payload machinery (port of the legacy executor's add-to-cart fix).

Swiggy's *response* shape (search_menu) and *request* shape (update_food_cart)
differ: requests need ``menu_item_id`` + flat ``{group_id, variation_id}`` rows,
responses carry ``itemId`` + nested ``{groupId, variations:[{id, …}]}`` blocks.
The planner naturally copies the response shape, which the MCP rejects with
INVALID_ITEM_IDS_IN_REQUEST. This module normalizes every common shape, builds
the in-plan menu index, auto-picks trivial variants, and summarizes real
variant choices so the turn can defer to the user instead of guessing.
"""

from __future__ import annotations

from typing import Any


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def collect_menu_index(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{item_id -> {name, hasVariants, variantsV2, variations, hasAddons}}``
    from a search_menu / get_restaurant_menu envelope (only rows carrying a
    variant signal — the index exists for variant injection/deferral)."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out
    for obj in _walk(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        iid_raw = (
            obj.get("menu_item_id")
            or obj.get("menuItemId")
            or obj.get("itemId")
            or obj.get("item_id")
        )
        iid = str(iid_raw).strip() if iid_raw is not None else ""
        if not iid or iid in out:
            continue
        has_variants = bool(obj.get("hasVariants"))
        v2 = obj.get("variantsV2") if isinstance(obj.get("variantsV2"), list) else []
        v1 = obj.get("variations") if isinstance(obj.get("variations"), list) else []
        if not (has_variants or v2 or v1):
            continue
        out[iid] = {
            "name": obj.get("name"),
            "hasVariants": has_variants,
            "variantsV2": v2,
            "variations": v1,
            "hasAddons": bool(obj.get("hasAddons")),
        }
    return out


def collect_item_ids(envelope: dict[str, Any]) -> set[str]:
    """Every ``menu_item_id`` in a search_menu / get_restaurant_menu envelope.

    Unlike ``collect_menu_index`` (variant-bearing rows only) this captures ALL
    items so the cart-add validator never drops a real no-variant item (e.g. a
    Garlic Bread the planner just searched and added)."""
    out: set[str] = set()
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out
    for obj in _walk(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        iid_raw = (
            obj.get("menu_item_id")
            or obj.get("menuItemId")
            or obj.get("itemId")
            or obj.get("item_id")
        )
        iid = str(iid_raw).strip() if iid_raw is not None else ""
        if iid:
            out.add(iid)
    return out


def is_choice_trivial(variants_v2: list[Any]) -> bool:
    """True when every variant group has ≤1 in-stock variation (no real choice)."""
    for group in variants_v2 or []:
        if not isinstance(group, dict):
            continue
        in_stock = [
            v
            for v in (group.get("variations") or [])
            if isinstance(v, dict) and v.get("inStock", 1) in (1, True)
        ]
        if len(in_stock) > 1:
            return False
    return True


def pick_default_variants(variants_v2: list[Any]) -> list[dict[str, Any]]:
    """One in-stock variation per group, in REQUEST shape ``[{group_id, variation_id}]``.

    Priority within a group: default && in-stock → first in-stock → first row."""
    out: list[dict[str, Any]] = []
    for group in variants_v2 or []:
        if not isinstance(group, dict):
            continue
        gid = str(group.get("groupId") or group.get("group_id") or "").strip()
        variations = group.get("variations")
        if not gid or not isinstance(variations, list) or not variations:
            continue
        rows = [v for v in variations if isinstance(v, dict)]
        chosen = next(
            (v for v in rows if v.get("default") in (1, True) and v.get("inStock", 1) in (1, True)),
            None,
        ) or next((v for v in rows if v.get("inStock", 1) in (1, True)), None)
        if chosen is None and rows:
            chosen = rows[0]
        vid_raw = (chosen or {}).get("id") or (chosen or {}).get("variantId")
        vid = str(vid_raw).strip() if vid_raw is not None else ""
        if vid:
            out.append({"group_id": gid, "variation_id": vid})
    return out


def summarize_variant_choices(
    item_id: str, item_name: Any, variants_v2: list[Any]
) -> dict[str, Any]:
    """Compact ``{item_id, item_name, groups:[{groupId, name, choices}]}`` block
    the narrator renders as a question ("8 inch ya 12 inch?")."""
    groups: list[dict[str, Any]] = []
    for group in variants_v2 or []:
        if not isinstance(group, dict):
            continue
        choices: list[dict[str, Any]] = []
        for v in group.get("variations") or []:
            if not isinstance(v, dict) or v.get("inStock", 1) not in (1, True):
                continue
            vid_raw = v.get("id") or v.get("variantId") or v.get("variant_id")
            if vid_raw is None:
                continue
            choice: dict[str, Any] = {"id": str(vid_raw).strip(), "name": str(v.get("name") or "")}
            if v.get("price") is not None:
                choice["price"] = v.get("price")
            choices.append(choice)
        if choices:
            groups.append(
                {
                    "groupId": str(group.get("groupId") or "").strip(),
                    "name": str(group.get("name") or "Choose"),
                    "choices": choices,
                }
            )
    return {"item_id": str(item_id), "item_name": item_name, "groups": groups}


def _flatten_variants_v2(v2: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in v2 or []:
        if not isinstance(entry, dict):
            continue
        gid = str(entry.get("group_id") or entry.get("groupId") or "").strip()
        if not gid:
            continue
        vid = entry.get("variation_id") or entry.get("variationId")
        if vid not in (None, ""):
            out.append({"group_id": gid, "variation_id": str(vid).strip()})
            continue
        for v in entry.get("variations") or []:
            if not isinstance(v, dict):
                continue
            vid2 = v.get("id") or v.get("variation_id") or v.get("variationId")
            if vid2 not in (None, ""):
                out.append({"group_id": gid, "variation_id": str(vid2).strip()})
    return out


def _flatten_legacy_variants(v1: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in v1 or []:
        if not isinstance(entry, dict):
            continue
        gid = entry.get("group_id") or entry.get("groupId")
        vid = entry.get("variation_id") or entry.get("variationId") or entry.get("id")
        if gid in (None, "") or vid in (None, ""):
            continue
        out.append({"group_id": str(gid).strip(), "variation_id": str(vid).strip()})
    return out


def _flatten_addons(addons: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in addons or []:
        if not isinstance(entry, dict):
            continue
        gid = entry.get("group_id") or entry.get("groupId")
        cid = entry.get("choice_id") or entry.get("choiceId") or entry.get("id")
        if gid in (None, "") or cid in (None, ""):
            continue
        row: dict[str, Any] = {"group_id": str(gid).strip(), "choice_id": str(cid).strip()}
        if entry.get("name"):
            row["name"] = entry["name"]
        if entry.get("price") is not None:
            row["price"] = entry["price"]
        out.append(row)
    return out


def normalize_cart_payload(args: dict[str, Any]) -> dict[str, Any]:
    """Coerce ``update_food_cart.cartItems[]`` into the strict request shape:
    snake_case ``menu_item_id`` plus flat variant/addon rows (in place)."""
    items = args.get("cartItems") if isinstance(args, dict) else None
    if not isinstance(items, list):
        return args
    for item in items:
        if not isinstance(item, dict):
            continue
        if not item.get("menu_item_id"):
            iid = item.get("itemId") or item.get("menuItemId") or item.get("item_id")
            if iid not in (None, ""):
                item["menu_item_id"] = str(iid)
        for key in ("itemId", "menuItemId", "item_id"):
            item.pop(key, None)
        v2 = item.get("variantsV2")
        if isinstance(v2, list):
            flattened = _flatten_variants_v2(v2)
            if flattened:
                item["variantsV2"] = flattened
            else:
                item.pop("variantsV2", None)
        v1 = item.get("variants") or item.get("variations")
        if isinstance(v1, list):
            flat_legacy = _flatten_legacy_variants(v1)
            if flat_legacy:
                item["variants"] = flat_legacy
            else:
                item.pop("variants", None)
            item.pop("variations", None)
        addons = item.get("addons")
        if isinstance(addons, list):
            flat_addons = _flatten_addons(addons)
            if flat_addons:
                item["addons"] = flat_addons
            else:
                item.pop("addons", None)
    return args
