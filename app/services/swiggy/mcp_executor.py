"""
Swiggy MCP (Food + Instamart) tool execution via mcp-remote + stdio.

Mirrors ``zomato.mcp_executor`` for the multi-turn carry-forward contract —
session-state placeholders, ``session_addresses`` (saved-address index for
zero-touch first turn + click-to-switch later turns), and
``session_restaurants`` (LRU of restaurants we've already shown so we can
skip a redundant fuzzy keyword search when the user picks one by name).

Kept as a separate module because Swiggy's address-prefetch tool and
response shapes differ from Zomato (e.g. ``get_addresses`` vs
``get_saved_addresses_for_user``, ``addressId`` vs ``address_id`` as the
canonical key, ``restaurants[].id`` vs ``results[].res_id``). Token
storage and OAuth stay shared with the Zomato module — mcp-remote uses
the same ``MCP_REMOTE_CONFIG_DIR`` layout regardless of provider.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("SWIGGY_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("SWIGGY_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30")
)

_DEFAULT_SWIGGY_FOOD_MCP_URL = "https://mcp.swiggy.com/food"

# Keep more than the typical user-visible result list (5–8 rows) so a
# follow-up turn that references an earlier search ("actually try the
# second one I saw") can still resolve. Mirrors ZOMATO_REMEMBERED_RESTAURANT_CAP.
_MAX_REMEMBERED_RESTAURANTS = max(
    1, int(os.getenv("SWIGGY_REMEMBERED_RESTAURANT_CAP", "8"))
)

# Reuse Zomato helpers — the placeholder substitution machinery, MCP env
# setup, and recent-restaurants merge are provider-agnostic.
from app.services.zomato.mcp_executor import (  # noqa: E402
    _PLACEHOLDER_ALIASES as _BASE_PLACEHOLDER_ALIASES,
)
from app.services.zomato.mcp_executor import (  # noqa: E402
    _humanize_mcp_failure,
    _mcp_remote_config_dir,
    _mentions_placeholder,
    _merge_recent_restaurants,
    _stdio_env,
    _tool_result_payload,
    _unresolved_placeholders,
    _walk_json,
    ensure_mcp_tokens_on_disk,
    repo_root_from_settings,
    substitute_placeholders,
)

_SWIGGY_RESTAURANT_EXTRA_ALIASES: tuple[str, ...] = (
    "swiggy_restaurant_id",
    "swiggyRestaurantId",
    "store_id",
    "storeId",
)

_SWIGGY_PLACEHOLDER_ALIASES: dict[str, tuple[str, ...]] = {
    **_BASE_PLACEHOLDER_ALIASES,
    "__RESTAURANT_ID__": _BASE_PLACEHOLDER_ALIASES["__RESTAURANT_ID__"] + _SWIGGY_RESTAURANT_EXTRA_ALIASES,
}

# Placeholders worth carrying ACROSS turns. Item-level IDs
# (``__MENU_ITEM_ID__`` / ``__ITEM_ID__``) are deliberately omitted: Swiggy
# item ids are outlet-specific so caching them silently breaks the next
# turn after an address switch or restaurant pivot ("add another
# margherita" would try to add the Kalkaji Pizza Hut margherita while the
# user is now on the Gurgaon outlet). They're still learned IN-plan so the
# planner can chain ``search_menu`` → ``update_food_cart`` in one shot.
_SWIGGY_SESSION_PERSISTENT_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "__ADDRESS_ID__",
        "__RESTAURANT_ID__",
        "__CART_ID__",
        "__ORDER_ID__",
    }
)

# Tools whose execution invalidates a carried-forward placeholder.
# Mirrors zomato._TOOL_ISSUES_PLACEHOLDER: when the planner re-runs one
# of these (user pivoted to a new restaurant, placed a fresh order, …),
# the value seeded from a prior turn is stale and would silently win
# over the new one because ``_learn_swiggy_placeholders`` is first-seen.
# Clearing before the call lets this step's response repopulate the slot.
_SWIGGY_TOOL_ISSUES_PLACEHOLDER: dict[str, tuple[str, ...]] = {
    # Each search picks a new restaurant from the user's keyword. Without
    # clearing, a __RESTAURANT_ID__ carried from turn N (e.g. Domino's)
    # silently survives a turn N+1 pivot ("show me Pizza Hut") because
    # the in-plan ``search_menu`` step substitutes the STALE id before
    # this step's response can update it.
    "search_restaurants": ("__RESTAURANT_ID__",),
    "place_food_order": ("__ORDER_ID__",),
}


def _learn_swiggy_placeholders(values: dict[str, str], tool_payload: dict[str, Any]) -> None:
    data = tool_payload.get("data")
    for obj in _walk_json(data):
        if not isinstance(obj, dict):
            continue
        for placeholder, aliases in _SWIGGY_PLACEHOLDER_ALIASES.items():
            if placeholder in values:
                continue
            for alias in aliases:
                val = obj.get(alias)
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    values[placeholder] = text
                    break


def _swiggy_addresses_from_envelope(tool_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the raw saved-address list out of a Swiggy ``get_addresses`` envelope.

    Tolerant of the three wrapper shapes Swiggy uses across endpoints:
    ``{"addresses": [...]}``, ``{"user_addresses": [...]}``,
    ``{"saved_addresses": [...]}``, and the (rare) nested
    ``{"data": {"addresses": [...]}}`` shape. Returns a *flat* list of
    address dicts; downstream helpers (``_find_best_address_id_swiggy``,
    ``_extract_address_rows_from_swiggy_envelope``) read from this single
    canonical list.
    """
    blob = tool_payload.get("data") if isinstance(tool_payload, dict) else None
    if blob is None:
        return []
    candidates: list[dict[str, Any]] = []
    data_dict = blob if isinstance(blob, dict) else (blob[0] if isinstance(blob, list) and blob else {})
    if isinstance(data_dict, dict):
        for key in ("addresses", "user_addresses", "saved_addresses"):
            raw = data_dict.get(key)
            if isinstance(raw, list):
                candidates.extend([a for a in raw if isinstance(a, dict)])
    if not candidates:
        # Fallback walk — handle deeper nesting where MCP wraps the list
        # under an intermediate ``result``/``data`` envelope.
        for obj in _walk_json(blob):
            if not isinstance(obj, dict):
                continue
            for key in ("addresses", "user_addresses", "saved_addresses"):
                raw = obj.get(key)
                if isinstance(raw, list):
                    candidates.extend([a for a in raw if isinstance(a, dict)])
            if candidates:
                break
    return candidates


def _find_best_address_id_swiggy(
    tool_payload: dict[str, Any],
    location_text: str | None = None,
) -> str | None:
    """Resolve a delivery address id from a Swiggy ``get_addresses`` payload.

    Match priority: exact tag/label match → partial address-line match →
    first row (Swiggy returns addresses sorted by last-order recency so
    the first one is the user's most-likely-correct default).
    """
    addresses = _swiggy_addresses_from_envelope(tool_payload)
    if not addresses:
        return None

    target = (location_text or "").strip().lower()
    if target:
        for addr in addresses:
            for key in ("addressTag", "address_tag", "addressCategory", "address_category", "name", "label"):
                val = str(addr.get(key) or "").strip().lower()
                if val == target:
                    aid = addr.get("addressId") or addr.get("address_id") or addr.get("id")
                    if aid:
                        return str(aid).strip()
        for addr in addresses:
            line = str(addr.get("addressLine") or addr.get("address_line") or "").strip().lower()
            if target in line:
                aid = addr.get("addressId") or addr.get("address_id") or addr.get("id")
                if aid:
                    return str(aid).strip()

    aid = addresses[0].get("addressId") or addresses[0].get("address_id") or addresses[0].get("id")
    return str(aid).strip() if aid else None


def _first_address_id_swiggy(tool_payload: dict[str, Any]) -> str | None:
    return _find_best_address_id_swiggy(tool_payload)


def _extract_address_rows_from_swiggy_envelope(
    tool_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Normalize Swiggy saved-address rows into the same shape Zomato emits.

    Output rows always carry ``address_id`` + ``label`` (the two fields
    the frontend and response LLM actually need), with all upstream
    Swiggy fields (``addressTag``, ``addressLine``, ``locality``, …)
    riding along untouched so other consumers (analytics, click-to-switch
    chips, future filters) can still read them.
    """
    addresses = _swiggy_addresses_from_envelope(tool_payload)
    rows: list[dict[str, Any]] = []
    for item in addresses:
        aid = item.get("addressId") or item.get("address_id") or item.get("id")
        if aid is None:
            continue
        sid = str(aid).strip()
        if not sid:
            continue
        tag = str(
            item.get("addressTag")
            or item.get("address_tag")
            or item.get("addressCategory")
            or item.get("address_category")
            or ""
        ).strip()
        line = str(
            item.get("addressLine")
            or item.get("address_line")
            or item.get("location_name")
            or item.get("formatted_address")
            or item.get("locality")
            or ""
        ).strip()
        if tag and line and line.lower() != tag.lower():
            label = f"{tag} — {line}"
        else:
            label = line or tag or sid
        rows.append(
            {
                **item,
                "address_id": sid,
                "label": label,
            }
        )
    return rows


def _collect_restaurants_from_swiggy_envelope(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ``(res_id, name, rating?, distanceKm?)`` rows from a Swiggy
    ``search_restaurants`` envelope.

    Swiggy returns hits under ``data.restaurants[]`` with ``id`` as the
    canonical key (a numeric outlet id) and rich metadata like
    ``availabilityStatus``, ``rating``, ``distanceKm``, ``eta``. We keep
    a small subset of those signals so the planner has enough context to
    pick the right one when the user references "the first one" or "Pizza
    Hut" by name later.
    """
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return []
    data = envelope.get("data")
    candidates: list[Any] = []
    if isinstance(data, dict) and isinstance(data.get("restaurants"), list):
        candidates = data["restaurants"]
    # Some Swiggy endpoints nest results one level deeper.
    if not candidates and isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else None
        if isinstance(inner, dict) and isinstance(inner.get("restaurants"), list):
            candidates = inner["restaurants"]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidates:
        if not isinstance(row, dict):
            continue
        rid_raw = row.get("id") or row.get("restaurantId") or row.get("restaurant_id")
        name = row.get("name") or row.get("restaurant_name") or row.get("restaurantName")
        if rid_raw is None or not isinstance(name, str) or not name.strip():
            continue
        rid = str(rid_raw).strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        entry: dict[str, Any] = {"res_id": rid, "name": name.strip()}
        for k_src, k_dst in (
            ("rating", "rating"),
            ("eta", "eta"),
            ("distanceKm", "distance_km"),
            ("distance_km", "distance_km"),
            ("availabilityStatus", "availability_status"),
        ):
            v = row.get(k_src)
            if v is not None and k_dst not in entry:
                entry[k_dst] = v
        out.append(entry)
    return out


def _learn_swiggy_restaurant_from_search(envelope: dict[str, Any], values: dict[str, str]) -> None:
    """Pick first restaurant id from ``search_restaurants`` for in-plan
    chaining (so the very next ``search_menu`` step in the same plan
    resolves ``__RESTAURANT_ID__`` without needing a session lookup)."""
    if "__RESTAURANT_ID__" in values:
        return
    if envelope.get("is_error"):
        return
    data = envelope.get("data")
    if not isinstance(data, dict):
        return
    rests = data.get("restaurants")
    if not (isinstance(rests, list) and rests and isinstance(rests[0], dict)):
        return
    rid = rests[0].get("id") or rests[0].get("restaurant_id") or rests[0].get("restaurantId")
    if rid is not None:
        s = str(rid).strip()
        if s:
            values["__RESTAURANT_ID__"] = s


# Keys the planner uses to target a specific restaurant in tool args.
# When any of these is a *literal* numeric string (not a placeholder)
# the planner has explicitly pivoted to that restaurant — update the
# session so downstream menu / cart / order tools all align.
_SWIGGY_RESTAURANT_ID_KEYS: tuple[str, ...] = (
    "restaurantId",
    "restaurant_id",
    "restaurantIdOfAddedItem",
)


def _learn_swiggy_restaurant_from_literal_args(
    tool: str,
    base_args: dict[str, Any],
    values: dict[str, str],
) -> None:
    """Refresh ``__RESTAURANT_ID__`` when the planner pins a tool to a
    literal restaurant id.

    Why this exists: the planner picks restaurants in two ways — (a) by
    re-running ``search_restaurants`` (handled by ``_TOOL_ISSUES_PLACEHOLDER``
    + ``_learn_swiggy_restaurant_from_search``), or (b) by pulling a known
    res_id straight from ``session_restaurants`` and pasting it into a
    menu / cart tool. Path (b) used to leave ``__RESTAURANT_ID__`` stuck
    on whatever turn 1 picked — so two turns later a placeholder-based
    ``update_food_cart`` would target the wrong restaurant and Swiggy
    would reject the cart with ``INVALID_ITEM_IDS_IN_REQUEST``.

    Only fires on menu / cart / order tools — calls that semantically
    "act on this restaurant" — so a generic ``get_addresses`` or similar
    doesn't accidentally rewrite the active restaurant.
    """
    if tool not in {
        "search_menu",
        "get_restaurant_menu",
        "get_restaurant_categories",
        "update_food_cart",
        "place_food_order",
    }:
        return
    if not isinstance(base_args, dict):
        return
    for key in _SWIGGY_RESTAURANT_ID_KEYS:
        raw = base_args.get(key)
        if raw in (None, ""):
            continue
        s = str(raw).strip()
        # Skip placeholders — those mean "use the session value", not
        # "override it". Only literal ids (digits or non-template strings)
        # signal an explicit pivot.
        if not s or (s.startswith("__") and s.endswith("__")):
            continue
        current = (values.get("__RESTAURANT_ID__") or "").strip()
        if s != current:
            values["__RESTAURANT_ID__"] = s
        return


# Tools whose responses are worth indexing for ``update_food_cart``
# auto-enrichment. We harvest ``{itemId -> {hasVariants, variantsV2, ...}}``
# from each so the executor can auto-pick a default variant when the
# planner emits a bare ``{itemId, quantity}`` cart row.
_MENU_PRODUCING_TOOLS = frozenset({"search_menu", "get_restaurant_menu"})


def _pick_default_variantsV2(variants_v2: list[Any]) -> list[dict[str, Any]]:
    """Pick one in-stock variation per group from a Swiggy ``variantsV2`` block.

    Selection priority within each group: ``default==1 && inStock==1`` →
    first ``inStock==1`` → first row. Returns the *request-shape* Swiggy
    MCP expects for ``update_food_cart.cartItems[].variantsV2``:
    ``[{group_id, variation_id}]`` (flat snake_case rows — verified via
    the ``swiggy_e2e_probe.py`` end-to-end test against the live MCP).

    NOTE: the *response* shape from ``search_menu`` is the inverse —
    ``[{groupId, variations:[{id, name, …}]}]`` — so we have to translate
    here, not pass through. Sending the response shape back as a request
    triggers ``INVALID_ITEM_IDS_IN_REQUEST``.
    """
    out: list[dict[str, Any]] = []
    for group in variants_v2 or []:
        if not isinstance(group, dict):
            continue
        gid_raw = group.get("groupId") or group.get("group_id")
        if gid_raw is None:
            continue
        gid = str(gid_raw).strip()
        variations = group.get("variations")
        if not gid or not isinstance(variations, list) or not variations:
            continue
        chosen: dict[str, Any] | None = None
        for v in variations:
            if (
                isinstance(v, dict)
                and v.get("default") in (1, True)
                and v.get("inStock", 1) in (1, True)
            ):
                chosen = v
                break
        if chosen is None:
            for v in variations:
                if isinstance(v, dict) and v.get("inStock", 1) in (1, True):
                    chosen = v
                    break
        if chosen is None and isinstance(variations[0], dict):
            chosen = variations[0]
        if not chosen:
            continue
        vid_raw = chosen.get("id") or chosen.get("variantId") or chosen.get("variant_id")
        if vid_raw is None:
            continue
        vid = str(vid_raw).strip()
        if not vid:
            continue
        out.append({"group_id": gid, "variation_id": vid})
    return out


def _collect_swiggy_menu_index(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build ``{itemId -> {hasVariants, variantsV2, variations, hasAddons, name}}``
    from a ``search_menu`` / ``get_restaurant_menu`` response.

    Walks the envelope generically so it tolerates the slightly different
    shapes the two tools return (``data.items[]`` vs nested category
    arrays). Only rows that look like a real menu item (have a
    ``menu_item_id`` and some variant signal) are kept.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out
    for obj in _walk_json(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        iid_raw = (
            obj.get("menu_item_id")
            or obj.get("menuItemId")
            or obj.get("itemId")
            or obj.get("item_id")
        )
        if iid_raw is None:
            continue
        iid = str(iid_raw).strip()
        if not iid or iid in out:
            continue
        has_variants = bool(obj.get("hasVariants"))
        v2 = obj.get("variantsV2") if isinstance(obj.get("variantsV2"), list) else []
        v1 = obj.get("variations") if isinstance(obj.get("variations"), list) else []
        if not (has_variants or v2 or v1):
            # Plain menu row without customization signal — not useful for
            # variant injection; skip to keep the index small.
            continue
        out[iid] = {
            "name": obj.get("name"),
            "hasVariants": has_variants,
            "variantsV2": v2,
            "variations": v1,
            "hasAddons": bool(obj.get("hasAddons")),
        }
    return out


def _is_variant_choice_trivial(variants_v2: list[Any]) -> bool:
    """True when every variant group has ≤1 in-stock variation (no real
    user choice). Items with a real size/crust/quantity decision return
    False so we can defer and ask the user instead of silently picking."""
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


def _summarize_variant_choices(
    item_id: str, item_name: str | None, variants_v2: list[Any]
) -> dict[str, Any]:
    """Compact ``{item_id, item_name, groups: [{groupId, name, choices: [{id, name, price}]}]}``
    structure for the response LLM to render as user-facing chips/options."""
    groups: list[dict[str, Any]] = []
    for group in variants_v2 or []:
        if not isinstance(group, dict):
            continue
        choices: list[dict[str, Any]] = []
        for v in group.get("variations") or []:
            if not isinstance(v, dict):
                continue
            if v.get("inStock", 1) not in (1, True):
                continue
            vid_raw = v.get("id") or v.get("variantId") or v.get("variant_id")
            if vid_raw is None:
                continue
            choice: dict[str, Any] = {"id": str(vid_raw).strip(), "name": str(v.get("name") or "")}
            if v.get("price") is not None:
                choice["price"] = v.get("price")
            choices.append(choice)
        if not choices:
            continue
        groups.append(
            {
                "groupId": str(group.get("groupId") or "").strip(),
                "name": str(group.get("name") or "Choose"),
                "choices": choices,
            }
        )
    return {"item_id": str(item_id), "item_name": item_name, "groups": groups}


def _flatten_variantsV2_request(v2: list[Any]) -> list[dict[str, Any]]:
    """Coerce any ``variantsV2`` shape the planner produces into the strict
    *request* shape Swiggy MCP expects: ``[{group_id, variation_id}]``.

    Accepts inputs from any of these sources:
      * Already flat snake_case: ``{group_id, variation_id}``
      * Flat camelCase: ``{groupId, variationId}``
      * Response shape (nested): ``{groupId, variations:[{id, …}]}`` — one
        flat row is emitted per inner variation (planner is expected to
        have already pruned to a single chosen variation, but if multiple
        are listed we forward them all because the cart API tolerates it
        and the alternative is silently dropping a user pick).
    """
    out: list[dict[str, Any]] = []
    for entry in v2 or []:
        if not isinstance(entry, dict):
            continue
        gid = entry.get("group_id") or entry.get("groupId")
        if gid is None:
            continue
        gid_s = str(gid).strip()
        if not gid_s:
            continue
        vid = entry.get("variation_id") or entry.get("variationId")
        if vid not in (None, ""):
            out.append({"group_id": gid_s, "variation_id": str(vid).strip()})
            continue
        variations = entry.get("variations")
        if isinstance(variations, list):
            for v in variations:
                if not isinstance(v, dict):
                    continue
                vid2 = v.get("id") or v.get("variation_id") or v.get("variationId")
                if vid2 in (None, ""):
                    continue
                out.append({"group_id": gid_s, "variation_id": str(vid2).strip()})
    return out


def _flatten_legacy_variants_request(v1: list[Any]) -> list[dict[str, Any]]:
    """Coerce a legacy ``variations`` / ``variants`` block into the request
    shape ``[{group_id, variation_id}]``. Mirrors ``_flatten_variantsV2_request``
    but tolerates the (rare) legacy menu format where a Swiggy item exposes
    ``variations`` instead of ``variantsV2``.
    """
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


def _flatten_addons_request(addons: list[Any]) -> list[dict[str, Any]]:
    """Normalize addon rows to ``[{group_id, choice_id, name?, price?}]``."""
    out: list[dict[str, Any]] = []
    for entry in addons or []:
        if not isinstance(entry, dict):
            continue
        gid = entry.get("group_id") or entry.get("groupId")
        cid = entry.get("choice_id") or entry.get("choiceId") or entry.get("id")
        if gid in (None, "") or cid in (None, ""):
            continue
        row: dict[str, Any] = {
            "group_id": str(gid).strip(),
            "choice_id": str(cid).strip(),
        }
        if entry.get("name"):
            row["name"] = entry["name"]
        if entry.get("price") is not None:
            row["price"] = entry["price"]
        out.append(row)
    return out


def _normalize_swiggy_cart_payload(args: dict[str, Any]) -> None:
    """In-place normalization of an ``update_food_cart`` payload to the
    strict snake_case / flat shape Swiggy MCP requires.

    Why this exists: Swiggy's *response* shape (returned by ``search_menu``)
    and *request* shape (consumed by ``update_food_cart``) differ —
    request uses ``menu_item_id`` + flat ``{group_id, variation_id}`` rows,
    response uses ``itemId``/``menu_item_id`` + nested ``{groupId,
    variations:[{id, …}]}`` blocks. Without this normalizer the planner's
    natural shape — copied from what it sees in tool context — triggers
    ``INVALID_ITEM_IDS_IN_REQUEST`` from the MCP. We coerce every common
    shape (camelCase, nested, mixed) into the one the server actually
    accepts. Verified end-to-end via ``scripts/swiggy_e2e_probe.py``.
    """
    items = args.get("cartItems") if isinstance(args, dict) else None
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        # menu_item_id is REQUIRED and must be snake_case. Coalesce from
        # any of the four shapes the planner / search_menu might emit.
        if not item.get("menu_item_id"):
            iid = (
                item.get("itemId")
                or item.get("menuItemId")
                or item.get("item_id")
            )
            if iid not in (None, ""):
                item["menu_item_id"] = str(iid)
        # Drop the duplicate keys so the payload is clean and there's no
        # ambiguity if the server happens to read either form.
        for k in ("itemId", "menuItemId", "item_id"):
            item.pop(k, None)

        # variantsV2: flatten + snake_case. Skip / drop the field entirely
        # when nothing valid remains so Swiggy can auto-pick defaults.
        v2 = item.get("variantsV2")
        if isinstance(v2, list):
            flattened = _flatten_variantsV2_request(v2)
            if flattened:
                item["variantsV2"] = flattened
            else:
                item.pop("variantsV2", None)

        # Legacy variants/variations: collapse to ``variants`` (snake_case
        # flat). Some Swiggy items still ship with the ``variations``
        # field instead of ``variantsV2`` — request schema also accepts
        # the ``variants`` array.
        v1 = item.get("variants") or item.get("variations")
        if isinstance(v1, list):
            flat_legacy = _flatten_legacy_variants_request(v1)
            if flat_legacy:
                item["variants"] = flat_legacy
            else:
                item.pop("variants", None)
            item.pop("variations", None)

        # Addons: snake_case + drop incomplete rows.
        addons = item.get("addons")
        if isinstance(addons, list):
            flat_addons = _flatten_addons_request(addons)
            if flat_addons:
                item["addons"] = flat_addons
            else:
                item.pop("addons", None)


def _enrich_cart_items_with_variants(
    args: dict[str, Any],
    menu_index: dict[str, dict[str, Any]],
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    """Decide whether ``update_food_cart`` is ready to send.

    For each ``cartItems[]`` row that targets a ``hasVariants: true`` menu
    item without explicit variant info, the executor either:

    * **Trivial case** — exactly one in-stock variation per group: silently
      injects it (e.g. a drink that "has variants" but really only one
      size; common Swiggy schema quirk).
    * **Real choice** — any group has multiple in-stock options (Pizza Hut
      Margherita 8"/10"/12"): defers. The row is left untouched and the
      item is appended to ``pending_variant_choice`` for the response
      builder to surface to the user.

    Returns the list of items that still need a user choice. The caller
    is expected to skip the actual ``update_food_cart`` MCP call when this
    list is non-empty so we don't pin the user to a guessed default.

    Planner-supplied variants ALWAYS take precedence — we only touch
    rows that arrive without any variant field.
    """
    pending: list[dict[str, Any]] = []
    if not isinstance(args, dict) or not menu_index:
        return pending
    items = args.get("cartItems")
    if not isinstance(items, list):
        return pending
    injected: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("variantsV2") or item.get("variations") or item.get("variants"):
            continue
        # Planner may emit the item id under any of these keys depending
        # on whether it copied snake_case from search_menu response or
        # camelCase from the older system prompt.
        iid_raw = (
            item.get("menu_item_id")
            or item.get("itemId")
            or item.get("item_id")
            or item.get("menuItemId")
        )
        if iid_raw is None:
            continue
        iid = str(iid_raw).strip()
        meta = menu_index.get(iid)
        if not meta or not meta.get("hasVariants"):
            continue
        v2 = meta.get("variantsV2") or []
        if _is_variant_choice_trivial(v2):
            picked = _pick_default_variantsV2(v2)
            if picked:
                item["variantsV2"] = picked
                injected.append(
                    {"item_id": iid, "name": meta.get("name"), "variantsV2": picked}
                )
        else:
            pending.append(_summarize_variant_choices(iid, meta.get("name"), v2))
    if injected:
        diagnostics.setdefault("auto_variants_injected", []).extend(injected)
    if pending:
        diagnostics.setdefault("pending_variant_choice", []).extend(pending)
    return pending


_PREFETCH_ADDRESS_TOOLS = frozenset(
    {"get_addresses", "get_user_addresses", "get_saved_addresses_for_user"}
)

# Tools that take an address parameter — we'll inject __ADDRESS_ID__ as
# a default when the planner forgot to. Kept as a single set instead of
# four near-identical if-branches (the old executor had separate blocks
# for search_restaurants / search_menu / search_products / update_cart
# that all did the same thing).
_ADDRESS_TAKING_TOOLS = frozenset(
    {
        "search_restaurants",
        "search_menu",
        "get_restaurant_menu",
        "get_food_cart",
        "update_food_cart",
        "place_food_order",
        "fetch_food_coupons",
        "apply_food_coupon",
        "get_food_orders",
        "search_products",
        "your_go_to_items",
        "update_cart",
    }
)


def _ensure_address_keys(args: dict[str, Any]) -> dict[str, Any]:
    """Inject ``addressId`` / ``address_id`` placeholders when both are missing."""
    out = dict(args)
    has_address = bool(
        str(out.get("addressId") or "").strip()
        or str(out.get("address_id") or "").strip()
    )
    if not has_address:
        out["addressId"] = "__ADDRESS_ID__"
        out["address_id"] = "__ADDRESS_ID__"
    return out


async def _run_swiggy_session_inner(
    tool_plan: list[dict[str, Any]],
    effective_address_id: str | None,
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None = None,
    location_text: str | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:
        raise RuntimeError(
            "Python MCP SDK is not installed for this interpreter. Use Python 3.10+ "
            "and install requirements.txt, or run the Lambda Python 3.12 container."
        ) from e

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_address_id": effective_address_id,
        "requested_location_text": location_text,
        "executor": "swiggy.mcp_executor",
        "saved_address_prefetch_tool": "get_addresses",
    }

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError(
            "mcp-remote is not installed — run npm install at the repo root."
        )

    # Cold-container guard: hydrate /tmp/.mcp-auth from Secrets Manager when
    # the current-version tokens file isn't already present locally.
    ensure_mcp_tokens_on_disk(repo_root, mcp_url=mcp_url, user_id=user_id)

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    resolved_id = (effective_address_id or "").strip() or None
    # Seed placeholder_values from prior-turn carry-forward (cart_id,
    # order_id, restaurant_id, address_id, …). The current-turn
    # address_id from the request always wins over a stale seed.
    placeholder_values: dict[str, str] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k.startswith("__") and k.endswith("__"):
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()

    # Track where the active ``__ADDRESS_ID__`` came from. The response
    # LLM uses this to decide whether to nudge the user about the active
    # address ("auto" / "session_addresses" → mention it; "request" /
    # "session_state" / "switched" → stay quiet, the user just acted on
    # it). Same contract as ``zomato.mcp_executor``.
    address_source: str | None = None
    if resolved_id:
        placeholder_values["__ADDRESS_ID__"] = resolved_id
        address_source = "request"
    elif placeholder_values.get("__ADDRESS_ID__"):
        resolved_id = placeholder_values["__ADDRESS_ID__"]
        address_source = "session_state"

    # Carry-forward saved-address list: the {address_id, label, …} rows
    # the frontend echoes back across turns. If we don't already know an
    # address from the request or session_state, eagerly pick the first
    # one — this is what makes turn-1 of a fresh chat run zero-touch
    # without the user clicking "Fetch addresses" anywhere.
    seed_addresses: list[dict[str, Any]] = []
    if isinstance(session_addresses, list):
        for entry in session_addresses:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id") or entry.get("addressId") or entry.get("id")
            if aid is None:
                continue
            sid = str(aid).strip()
            if not sid:
                continue
            seed_addresses.append({**entry, "address_id": sid})
    if not resolved_id and seed_addresses:
        resolved_id = seed_addresses[0]["address_id"]
        placeholder_values["__ADDRESS_ID__"] = resolved_id
        address_source = "session_addresses"

    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())
    # Surface the seed list immediately so the pipeline can echo it back
    # even on turns where no address-fetching tool actually runs.
    if seed_addresses:
        diagnostics["session_addresses"] = list(seed_addresses)

    # Per-session restaurant memory — every (res_id, name) the user has
    # been shown, most-recent first. Seeded from the prior turn's
    # ``session_restaurants`` echo, refreshed by every
    # ``search_restaurants`` response, and surfaced back through
    # diagnostics so the pipeline can persist it for the next turn.
    recent_restaurants: list[dict[str, Any]] = []
    if isinstance(session_restaurants, list):
        for entry in session_restaurants:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("res_id")
            name = entry.get("name")
            if not isinstance(rid, str) or not rid.strip():
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            recent_restaurants.append({**entry, "res_id": rid.strip(), "name": name.strip()})

    prefetch_done = False
    prefetch_tool = "get_addresses"

    # Per-plan menu index: ``{itemId -> {hasVariants, variantsV2, ...}}``
    # populated from every menu-producing step's response. Powers the
    # auto-variant injection for ``update_food_cart`` below — without
    # this, Swiggy MCP rejects ``{itemId, quantity}`` rows for items
    # like Pizza Hut's Margherita that require a size selection.
    menu_index: dict[str, dict[str, Any]] = {}

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(
                session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS
            )

            step_index = 0

            async def prefetch_addresses() -> None:
                nonlocal resolved_id, prefetch_done, step_index, address_source
                if prefetch_done:
                    return
                prefetch_done = True
                r = await asyncio.wait_for(
                    session.call_tool(prefetch_tool, arguments={}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(r)
                step_results.append(
                    {
                        "tool": f"{prefetch_tool}(auto)",
                        "arguments": {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                nid = _find_best_address_id_swiggy(envelope, location_text=location_text)
                if nid:
                    resolved_id = nid
                    placeholder_values["__ADDRESS_ID__"] = nid
                    diagnostics["resolved_address_id"] = resolved_id
                    if address_source is None:
                        address_source = "auto"
                else:
                    diagnostics["auto_address_failure"] = (
                        f"Could not extract address_id from {prefetch_tool} response."
                    )
                # Learn the FULL saved-address list so future turns can
                # skip the MCP call entirely. Merge with whatever the
                # frontend already echoed so a partial response doesn't
                # truncate the index.
                fresh_rows = _extract_address_rows_from_swiggy_envelope(envelope)
                if fresh_rows:
                    existing_ids = {row["address_id"] for row in seed_addresses}
                    merged = list(seed_addresses)
                    for row in fresh_rows:
                        if row["address_id"] not in existing_ids:
                            merged.append(row)
                            existing_ids.add(row["address_id"])
                    diagnostics["session_addresses"] = fresh_rows if not seed_addresses else merged
                _learn_swiggy_placeholders(placeholder_values, envelope)
                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

            for raw in tool_plan:
                if not isinstance(raw, dict):
                    continue
                tool = (raw.get("tool") or "").strip()
                if not tool:
                    continue
                if tool in _PREFETCH_ADDRESS_TOOLS:
                    prefetch_done = True

                # Invalidate placeholders that this tool re-issues. Must
                # happen BEFORE the loop substitutes carried-forward
                # values into later in-plan steps — otherwise a fresh
                # ``search_restaurants`` for "Pizza Hut" would still let
                # the prior turn's Domino's __RESTAURANT_ID__ leak into
                # this plan's ``search_menu`` substitution. The slot is
                # repopulated by ``_learn_swiggy_restaurant_from_search``
                # after the tool actually runs.
                _issued = _SWIGGY_TOOL_ISSUES_PLACEHOLDER.get(tool)
                if _issued:
                    for _ph in _issued:
                        placeholder_values.pop(_ph, None)

                base_args = copy.deepcopy(raw.get("arguments") or {})
                if tool in _ADDRESS_TAKING_TOOLS and isinstance(base_args, dict):
                    base_args = _ensure_address_keys(base_args)

                # Promote literal restaurantId in the planner's args to
                # the session BEFORE substitution. This way a follow-up
                # step that still uses ``__RESTAURANT_ID__`` (e.g. the
                # planner emitted literal id on search_menu but kept the
                # placeholder on update_food_cart) sees the fresh id.
                _learn_swiggy_restaurant_from_literal_args(
                    tool, base_args, placeholder_values
                )

                exec_args = substitute_placeholders(copy.deepcopy(base_args), placeholder_values)

                if tool not in _PREFETCH_ADDRESS_TOOLS and _mentions_placeholder(exec_args):
                    unresolved = _unresolved_placeholders(exec_args)
                    if "__ADDRESS_ID__" in unresolved:
                        await prefetch_addresses()
                        exec_args = substitute_placeholders(
                            copy.deepcopy(base_args), placeholder_values
                        )

                # Address-switching: when the planner emits a *literal*
                # addressId/address_id that differs from the carried
                # __ADDRESS_ID__, treat it as a user-initiated switch
                # ("use my work address"). Update placeholder_values so
                # subsequent tools in this plan AND the next turn carry
                # the new one. Without this, the current tool runs
                # against the new address (correct) but session_state
                # silently reverts on the next turn.
                if (
                    isinstance(exec_args, dict)
                    and tool not in _PREFETCH_ADDRESS_TOOLS
                ):
                    literal_aid = exec_args.get("addressId") or exec_args.get("address_id")
                    if isinstance(literal_aid, str) and literal_aid.strip():
                        sid = literal_aid.strip()
                        if not (sid.startswith("__") and sid.endswith("__")):
                            current = placeholder_values.get("__ADDRESS_ID__")
                            if current != sid:
                                placeholder_values["__ADDRESS_ID__"] = sid
                                resolved_id = sid
                                address_source = "switched"
                                diagnostics["resolved_address_id"] = sid
                                logger.info(
                                    "swiggy.address_switch tool=%s from=%s to=%s",
                                    tool,
                                    current,
                                    sid,
                                )

                if _mentions_placeholder(exec_args):
                    unresolved = _unresolved_placeholders(exec_args)
                    diagnostics.setdefault("skipped_tools", []).append(
                        {
                            "tool": tool,
                            "reason": "placeholder_unresolved",
                            "placeholders": unresolved,
                        }
                    )
                    continue

                # Variant resolution for cart updates. Trivial single-
                # option items get auto-injected; items with a real user
                # choice (Margherita 8"/10"/12") cause the cart call to
                # be DEFERRED — diagnostics carry the variant menu and
                # the response builder asks the user to pick. The next
                # turn the planner will emit a fresh ``update_food_cart``
                # with explicit ``variantsV2`` for the chosen size.
                if tool == "update_food_cart" and isinstance(exec_args, dict):
                    # Normalize whatever the planner produced into the
                    # strict snake_case + flat shape Swiggy MCP expects.
                    # Done BEFORE enrichment so the variant-presence
                    # check (`item.get("variantsV2")`) sees the normalized
                    # field name and the auto-injection only fires for
                    # rows that truly need it.
                    _normalize_swiggy_cart_payload(exec_args)
                    pending_choices = _enrich_cart_items_with_variants(
                        exec_args, menu_index, diagnostics
                    )
                    if pending_choices:
                        diagnostics.setdefault("skipped_tools", []).append(
                            {
                                "tool": tool,
                                "reason": "awaiting_variant_choice",
                                "items": [p.get("item_name") for p in pending_choices],
                            }
                        )
                        # Don't call MCP — the assistant will ask the
                        # user which variant they want, and the next
                        # turn's plan will retry with explicit variants.
                        continue

                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=exec_args or {}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(result)
                step_results.append(
                    {
                        "tool": tool,
                        "arguments": exec_args or {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1

                if tool in _PREFETCH_ADDRESS_TOOLS:
                    if resolved_id is None:
                        nid = _first_address_id_swiggy(envelope)
                        if nid:
                            resolved_id = nid
                            placeholder_values["__ADDRESS_ID__"] = nid
                            diagnostics["resolved_address_id"] = resolved_id
                            if address_source is None:
                                address_source = "auto"
                    # Refresh the saved-address index from the explicit
                    # call too, so the next turn carries the fresh list.
                    try:
                        fresh_rows = _extract_address_rows_from_swiggy_envelope(envelope)
                    except Exception as e:
                        logger.info("swiggy address index extraction failed: %s", e)
                        fresh_rows = []
                    if fresh_rows:
                        diagnostics["session_addresses"] = fresh_rows

                _learn_swiggy_placeholders(placeholder_values, envelope)

                if tool in _MENU_PRODUCING_TOOLS and not envelope.get("is_error"):
                    # Harvest variants/addons for any items the planner
                    # might reference in a downstream ``update_food_cart``
                    # in this same plan.
                    try:
                        menu_index.update(_collect_swiggy_menu_index(envelope))
                    except Exception as e:
                        logger.info("swiggy menu index extraction failed: %s", e)

                if tool == "search_restaurants" and not envelope.get("is_error"):
                    # In-plan chaining: let the next menu step in this
                    # plan resolve __RESTAURANT_ID__ from the first hit.
                    _learn_swiggy_restaurant_from_search(envelope, placeholder_values)
                    # Cross-turn memory: harvest the full list of hits
                    # into the LRU so a follow-up turn can pick by name
                    # without re-running an unreliable keyword search.
                    try:
                        new_restos = _collect_restaurants_from_swiggy_envelope(envelope)
                    except Exception as e:
                        logger.info("swiggy restaurant index extraction failed: %s", e)
                        new_restos = []
                    if new_restos:
                        recent_restaurants = _merge_recent_restaurants(
                            recent_restaurants,
                            new_restos,
                            cap=_MAX_REMEMBERED_RESTAURANTS,
                        )

                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

    diagnostics["resolved_address_id"] = resolved_id
    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    if address_source:
        diagnostics["address_source"] = address_source
    # Surface placeholder *values* so the pipeline can hand them back to
    # the frontend as ``session_state`` and seed the next turn. This is
    # the bridge that lets a Swiggy cart created in turn N be checked
    # out in turn N+1 without the LLM ever seeing the raw cart_id.
    #
    # Only persist the small set that's stable across turns. Item-level
    # IDs (``__MENU_ITEM_ID__`` / ``__ITEM_ID__``) are NOT persisted —
    # they're outlet-specific so a stale value from turn N would silently
    # mis-resolve on turn N+1 after an address switch (e.g. Pizza Hut
    # Kalkaji's Margherita id ≠ Pizza Hut Gurgaon's Margherita id). They
    # were still useful in-plan for chaining ``search_menu`` →
    # ``update_food_cart``; that path is unaffected.
    diagnostics["session_placeholders"] = {
        k: v
        for k, v in placeholder_values.items()
        if k in _SWIGGY_SESSION_PERSISTENT_PLACEHOLDERS
    }
    if recent_restaurants:
        diagnostics["session_restaurants"] = list(recent_restaurants)
    return step_results, diagnostics


def run_swiggy_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    location_text: str | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute a Swiggy MCP tool plan (Food or Instamart URL)."""
    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("SWIGGY_FOOD_MCP_URL") or _DEFAULT_SWIGGY_FOOD_MCP_URL).strip()
    node = node_binary or os.getenv("NODE_BINARY", "node")

    cleaned: list[dict[str, Any]] = []
    for s in tool_plan:
        if isinstance(s, dict):
            tn = str(s.get("tool") or "").strip()
            if tn:
                cleaned.append(
                    {
                        "tool": tn,
                        "arguments": dict(s.get("arguments") or {}),
                    }
                )

    try:
        steps, diagnostics = asyncio.run(
            _run_swiggy_session_inner(
                cleaned,
                address_id.strip() if address_id else None,
                root,
                url,
                node,
                user_id,
                location_text=location_text,
                session_placeholders=session_placeholders,
                session_restaurants=session_restaurants,
                session_addresses=session_addresses,
            )
        )
        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
    except BaseException as e:
        logger.exception("Swiggy MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e, url),
            "steps": [],
            "diagnostics": {
                "mcp_remote_config_dir": str(_mcp_remote_config_dir(root, user_id=user_id)),
            },
        }


def list_swiggy_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """List tools from a Swiggy MCP HTTP endpoint (same wire protocol as Zomato)."""
    from app.services.zomato.mcp_executor import list_mcp_tools_sync

    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("SWIGGY_FOOD_MCP_URL") or _DEFAULT_SWIGGY_FOOD_MCP_URL).strip()
    return list_mcp_tools_sync(
        repo_root=root,
        mcp_url=url,
        node_binary=node_binary,
        user_id=user_id,
    )
