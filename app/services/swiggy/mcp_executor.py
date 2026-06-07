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
    user_geo: dict[str, Any] | None = None,
) -> str | None:
    """Resolve a delivery address id from a Swiggy ``get_addresses`` payload.

    Match priority: exact tag/label match → partial address-line match →
    nearest saved address to the device location (issue #18) → first row
    (Swiggy returns addresses sorted by last-order recency so the first one
    is the user's most-likely-correct default).
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

    # Nearest saved address to the device location — fallback heuristic only.
    if user_geo:
        try:
            from app.services.address_nearest import nearest_address_id

            nearest = nearest_address_id(addresses, user_geo)
            if nearest:
                return nearest
        except Exception:
            logger.debug("swiggy nearest-address pick failed", exc_info=True)

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


# Tools whose responses are worth indexing into ``session_menu`` for the
# next-turn planner. Mirrors ``zomato._MENU_PRODUCING_TOOLS``: harvest
# every menu item the user has been shown so the planner can next turn
# emit a literal ``menu_item_id`` + correct ``variantsV2`` / ``addons``
# from the cache, without re-running a noisy ``search_menu``.
_MENU_PRODUCING_TOOLS = frozenset({"search_menu", "get_restaurant_menu"})

# Cart-producing tools whose envelopes carry the full per-restaurant cart
# state. Mirrors ``zomato._extract_cart_snapshot`` capture points.
_CART_PRODUCING_TOOLS = frozenset({"update_food_cart", "get_food_cart"})

# Item-cache cap. Mirrors ``zomato._MAX_REMEMBERED_MENU_ITEMS`` — enough
# headroom for two restaurants' worth of items the user might cross-shop
# in a single chat without the planner needing a fresh ``search_menu``.
_MAX_REMEMBERED_MENU_ITEMS = max(
    1, int(os.getenv("SWIGGY_REMEMBERED_MENU_ITEMS_CAP", "48"))
)


# ---------------------------------------------------------------------------
# Defensive cart scrub (replaces the heavy `_normalize_swiggy_cart_payload` +
# `_enrich_cart_items_with_variants` machinery). Mirrors zomato's
# `_scrub_cart_items_for_zomato`: validate the planner's output, drop rows
# that obviously can't succeed, surface them in diagnostics. NO code-side
# variant matching, NO reshape — the planner is responsible for emitting
# the strict request shape Swiggy MCP expects.
# ---------------------------------------------------------------------------
def _is_valid_swiggy_menu_item_id(value: Any) -> bool:
    """A Swiggy menu_item_id is a non-empty string that isn't a placeholder.

    Empirically Swiggy uses bare numeric strings (e.g. ``"61057692"``) — we
    don't enforce digits-only because some outlets ship alphanumeric ids
    and we'd rather forward an unfamiliar shape than silently swallow it.
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    if s.startswith("__") and s.endswith("__"):
        return False
    return True


def _scrub_swiggy_cart_items(
    args: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop ``cartItems[]`` rows missing a valid ``menu_item_id``.

    Returns ``(scrubbed_args, dropped_rows)`` — ``dropped_rows`` captures
    the rows we refused to send so the caller can stamp a diagnostic and
    the response LLM can tell the user which item failed.

    Why drop on the client side at all? The planner LLM occasionally
    sneaks a leftover ``__MENU_ITEM_ID__`` placeholder string into a row
    when it loses track of the in-plan substitution. That reaches Swiggy
    as a string the server can't resolve and the whole cart update fails
    with ``INVALID_ITEM_IDS_IN_REQUEST``. Catching the mix-up here lets
    the rest of the (correct) rows go through.

    Everything else — variant selection, addon picking, shape coercion —
    is the planner's responsibility. The system prompt tells it exactly
    which keys to emit; this scrub only enforces "the id must look real".
    """
    items = args.get("cartItems") if isinstance(args.get("cartItems"), list) else None
    if not items:
        return args, []
    out: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        # Accept any of the four key shapes the planner might emit; the
        # request schema requires ``menu_item_id`` so we normalise here
        # for that single field (the rest stays as-is so a planner mistake
        # surfaces as a real Swiggy error the user can see).
        iid_raw = (
            raw.get("menu_item_id")
            or raw.get("menuItemId")
            or raw.get("itemId")
            or raw.get("item_id")
        )
        if not _is_valid_swiggy_menu_item_id(iid_raw if isinstance(iid_raw, str) else str(iid_raw or "")):
            dropped.append(
                {
                    "menu_item_id": iid_raw,
                    "quantity": raw.get("quantity"),
                    "reason": "invalid_menu_item_id",
                }
            )
            continue
        row = dict(raw)
        row["menu_item_id"] = str(iid_raw).strip()
        for k in ("itemId", "menuItemId", "item_id"):
            row.pop(k, None)
        out.append(row)
    args["cartItems"] = out
    return args, dropped


# ---------------------------------------------------------------------------
# Session-menu LRU — mirrors zomato._collect_menu_index_from_envelope +
# _merge_session_menu. The planner reads it next turn via the "Menu items
# already shown" block so it can emit literal ``menu_item_id`` + the right
# ``variantsV2`` / ``addons`` rows without re-running ``search_menu``.
# ---------------------------------------------------------------------------
def _collect_swiggy_menu_index_from_envelope(
    envelope: dict[str, Any],
    *,
    res_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Walk a Swiggy menu envelope and return ``{menu_item_id -> entry}``.

    Handles both ``search_menu`` (``data.items[]``) and
    ``get_restaurant_menu`` (``data.categories[].items[]``) shapes via a
    generic walk — each item that exposes a ``menu_item_id`` (or
    ``itemId``) and a ``name`` becomes one cache row. The entry preserves
    the **response shape** of ``variantsV2`` / ``variations`` / ``addons``
    verbatim so the planner can pick the right ``{group_id, variation_id}``
    and ``{group_id, choice_id}`` rows directly from what it sees.

    Each row carries an ``item_name`` alias (in addition to ``name``) and
    a ``variant_id`` alias (= ``menu_item_id``) so the pipeline's
    Zomato-shaped session_menu cleanup (which keys on ``variant_id``)
    accepts these rows unmodified.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return out
    rid = (res_id or "").strip() or None
    for obj in _walk_json(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        # Try the menu-item id keys first (search_menu shape), then fall
        # back to bare ``id`` (get_restaurant_menu shape). For the bare-id
        # case we require at least one menu-item-discriminator field so
        # the walk doesn't accidentally index the parent ``restaurant``
        # or ``category`` blobs (they also carry ``id`` + ``name``).
        iid_raw = (
            obj.get("menu_item_id")
            or obj.get("menuItemId")
            or obj.get("itemId")
            or obj.get("item_id")
        )
        if iid_raw is None:
            bare_id = obj.get("id")
            if bare_id is None:
                continue
            # Discriminator: a real menu item always carries one of these
            # fields. Restaurants/categories never do.
            looks_like_item = (
                "price" in obj
                or "hasVariants" in obj
                or "hasAddons" in obj
                or "isVeg" in obj
                or "variantsV2" in obj
                or "variations" in obj
            )
            if not looks_like_item:
                continue
            iid_raw = bare_id
        iid = str(iid_raw).strip()
        if not iid or iid in out:
            continue
        name = obj.get("name") or obj.get("item_name")
        if not isinstance(name, str) or not name.strip():
            continue
        v2_raw = obj.get("variantsV2") if isinstance(obj.get("variantsV2"), list) else None
        v1_raw = obj.get("variations") if isinstance(obj.get("variations"), list) else None
        addons_raw = obj.get("addons") if isinstance(obj.get("addons"), list) else None
        entry: dict[str, Any] = {
            "menu_item_id": iid,
            # Alias so pipeline.py's session_menu cleanup (keyed on
            # ``variant_id``, designed for Zomato) accepts these rows.
            "variant_id": iid,
            "item_name": name.strip(),
            "name": name.strip(),
            "hasVariants": bool(obj.get("hasVariants")),
            "hasAddons": bool(obj.get("hasAddons")),
        }
        if rid:
            entry["res_id"] = rid
        for src_key, dst_key in (("price", "price"), ("finalPrice", "final_price"), ("inStock", "in_stock")):
            v = obj.get(src_key)
            if v is not None and dst_key not in entry:
                entry[dst_key] = v
        if v2_raw:
            entry["variantsV2"] = v2_raw
        if v1_raw:
            entry["variations"] = v1_raw
        if addons_raw:
            entry["addons"] = addons_raw
        out[iid] = entry
    return out


def _swiggy_session_menu_key(entry: dict[str, Any]) -> str:
    """Stable cache key ``(res_id, menu_item_id)`` lower-cased.

    Including ``res_id`` is critical for Swiggy: ``menu_item_id`` is
    outlet-scoped, so the same item id at Pizza Hut Kalkaji and Pizza Hut
    Gurgaon point to different rows — without ``res_id`` an address pivot
    would silently mis-resolve the cart line.
    """
    rid = str(entry.get("res_id") or "").strip()
    iid = str(entry.get("menu_item_id") or entry.get("variant_id") or "").strip()
    return f"{rid}|{iid}".lower()


def _swiggy_menu_index_to_session_entries(
    menu_index: dict[str, dict[str, Any]],
    res_id: str | None,
) -> list[dict[str, Any]]:
    """Snapshot a per-turn ``menu_index`` into a list ready for session storage."""
    out: list[dict[str, Any]] = []
    rid = (res_id or "").strip() or None
    for entry in menu_index.values():
        iid = str(entry.get("menu_item_id") or entry.get("variant_id") or "").strip()
        if not iid:
            continue
        snapshot = dict(entry)
        if rid and not snapshot.get("res_id"):
            snapshot["res_id"] = rid
        out.append(snapshot)
    return out


def _merge_swiggy_session_menu(
    existing: list[dict[str, Any]],
    new_entries: list[dict[str, Any]],
    *,
    cap: int = _MAX_REMEMBERED_MENU_ITEMS,
) -> list[dict[str, Any]]:
    """LRU merge — newest first, cap-trimmed. Newer entry wins on conflict
    (last menu envelope tends to be the most accurate for that outlet's
    current state)."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for e in new_entries:
        if not isinstance(e, dict):
            continue
        key = _swiggy_session_menu_key(e)
        if not key or key == "|":
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = e
    for e in existing or []:
        if not isinstance(e, dict):
            continue
        key = _swiggy_session_menu_key(e)
        if not key or key == "|":
            continue
        if key in by_key:
            # Prefer the new entry's response shape; only fall back to the
            # existing one when the new one is missing optional fields.
            merged = dict(by_key[key])
            for k, v in e.items():
                if k not in merged or merged.get(k) in (None, "", [], {}):
                    merged[k] = v
            by_key[key] = merged
        else:
            order.append(key)
            by_key[key] = e
    return [by_key[k] for k in order[:cap]]


# ---------------------------------------------------------------------------
# Session-cart snapshot — mirrors zomato._extract_cart_snapshot.
# Captured after every successful ``update_food_cart`` / ``get_food_cart``
# so the response LLM has ground-truth for "what's in the user's cart
# right now", and so the chat-side cart card can render from the snapshot
# even on later turns that don't touch the cart.
# ---------------------------------------------------------------------------
def _extract_swiggy_cart_snapshot(
    envelope: dict[str, Any],
    *,
    restaurant_name_hint: str | None = None,
    res_id_hint: str | None = None,
) -> dict[str, Any] | None:
    """Pull a normalized cart snapshot from a successful Swiggy cart envelope.

    Swiggy carts (like Zomato's) are single-restaurant: every successful
    ``update_food_cart`` mutates *the* cart for the active outlet. The
    snapshot is replacement state — the next successful cart envelope
    overwrites it whole.

    The Swiggy cart envelope (observed live, see
    ``/tmp/swiggy_cart_envelope_get_food_cart.json``) nests the real cart
    payload at ``envelope.data.data`` with this shape::

        {
          "cart_id": <int>,
          "restaurant": {"deliverySubtitle": "<eta + addr blurb>"},
          "items": [{"menu_item_id", "name", "quantity", "is_veg",
                     "subtotal", "total", "final_price", "in_stock",
                     "variants":[{"name","group_name",...}],
                     "addons":[{"name","group_name",...}]}],
          "item_count": <int>,
          "pricing": {"item_total", "delivery_charge",
                      "taxes_and_charges", "to_pay"},
          "offers": {"coupon_applied", "coupon_discount",
                     "free_delivery_applied"}
        }

    Swiggy does NOT echo the restaurant **name** back in the cart
    envelope — only the rid is implicit (matches the
    ``__RESTAURANT_ID__`` placeholder the planner used). The caller
    threads the active restaurant's name + rid through
    ``restaurant_name_hint`` / ``res_id_hint`` so the chat-side
    <CartCard /> header still reads "Pizza Hut" instead of "Restaurant".

    Output is shaped to match the Zomato ``SessionCart`` contract the
    frontend already understands (no separate Swiggy-only branch needed).

    Returns ``None`` when the envelope is an error or carries no usable
    cart shape (e.g. an empty cart from ``get_food_cart`` before the
    user has added anything).
    """
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    cart_root: dict[str, Any] | None = None
    for node in _walk_json(data):
        if (
            isinstance(node, dict)
            and (node.get("cart_id") or node.get("cartId") or node.get("orderId") or node.get("order_id"))
            and (
                node.get("cartItems")
                or node.get("cart_items")
                or node.get("items")
            )
        ):
            cart_root = node
            break
    if not cart_root:
        return None

    items_raw = (
        cart_root.get("cartItems")
        or cart_root.get("cart_items")
        or cart_root.get("items")
        or []
    )
    items: list[dict[str, Any]] = []
    for row in items_raw:
        if not isinstance(row, dict):
            continue
        iid = (
            row.get("menu_item_id")
            or row.get("menuItemId")
            or row.get("itemId")
            or row.get("item_id")
        )
        name = row.get("name") or row.get("item_name") or row.get("itemName")
        if not iid or not isinstance(name, str) or not name.strip():
            continue
        # Pricing on Swiggy is per-row:
        #   * ``subtotal`` = original (pre-promo) line total for ``quantity`` units
        #   * ``total``     = ``subtotal`` (kept for back-compat)
        #   * ``final_price`` = the actual chargeable line price (e.g. ₹159 vs
        #     subtotal ₹296 when a "ITEMS AT ₹X" promo hits)
        # The frontend's <CartItemList /> shows ``discounted_price`` (struck) vs
        # ``price`` (bold), matching the Zomato shape. Map ``final_price`` →
        # ``discounted_price`` when it's strictly less than the subtotal.
        original = row.get("subtotal") or row.get("total") or row.get("price")
        discounted = row.get("final_price")
        is_veg_raw = row.get("is_veg")
        tags: list[str] = []
        if str(is_veg_raw) in {"1", "true", "True"}:
            tags.append("veg")
        # Roll the chosen Swiggy variants up into the item description so
        # the cart card surfaces them ("Pizza: Classic Corn / 1st
        # Appetizer: Half Classic Garlic Breadstix") — these are the
        # combo picks the user made, not separate addons.
        variant_parts: list[str] = []
        for v in row.get("variants") or []:
            if not isinstance(v, dict):
                continue
            vn = (v.get("name") or "").strip()
            gn = (v.get("group_name") or "").strip()
            if vn:
                variant_parts.append(f"{gn}: {vn}" if gn else vn)
        addons_clean: list[dict[str, Any]] = []
        for a in row.get("addons") or []:
            if not isinstance(a, dict):
                continue
            an = (a.get("name") or a.get("addon_name") or "").strip()
            if not an:
                continue
            addons_clean.append({
                "id": str(a.get("choice_id") or a.get("id") or "").strip() or None,
                "group_id": str(a.get("group_id") or "").strip() or None,
                "name": an,
                "add_on_group_name": (a.get("group_name") or None),
                "quantity": a.get("quantity") or 1,
            })
        item_out: dict[str, Any] = {
            "menu_item_id": str(iid).strip(),
            # Alias so the frontend's Zomato-shaped <CartItemList /> key
            # extractor (``item.variant_id || item.item_id``) finds an id.
            "variant_id": str(iid).strip(),
            "item_name": name.strip(),
            "quantity": row.get("quantity") or 1,
            "item_tags": tags,
        }
        if isinstance(original, (int, float)) and original > 0:
            item_out["price"] = original
        if isinstance(discounted, (int, float)) and discounted > 0:
            item_out["discounted_price"] = discounted
        if variant_parts:
            item_out["description"] = " · ".join(variant_parts)
        img = row.get("imageUrl") or row.get("image_url")
        if isinstance(img, str) and img.strip():
            item_out["image_url"] = img.strip()
        if addons_clean:
            item_out["add_ons"] = addons_clean
        items.append(item_out)
    if not items:
        return None

    cart_id = (
        cart_root.get("cart_id")
        or cart_root.get("cartId")
        or cart_root.get("orderId")
        or cart_root.get("order_id")
    )
    res_id_raw = (
        cart_root.get("restaurantId")
        or cart_root.get("restaurant_id")
        or cart_root.get("res_id")
        or res_id_hint
    )
    restaurant_name_raw = (
        cart_root.get("restaurantName")
        or cart_root.get("restaurant_name")
        or restaurant_name_hint
    )
    # Build the Zomato-shaped ``charge_breakdown`` from Swiggy's
    # ``pricing`` block so the frontend's <PriceBreakdown /> renders a
    # familiar receipt without a per-provider branch.
    pricing = cart_root.get("pricing") if isinstance(cart_root.get("pricing"), dict) else {}
    item_total = pricing.get("item_total")
    delivery_charge = pricing.get("delivery_charge") or pricing.get("deliveryCharge")
    taxes_amount = pricing.get("taxes_and_charges") or pricing.get("taxes")
    to_pay = pricing.get("to_pay") or pricing.get("toPay") or pricing.get("total")
    base_charges: list[dict[str, Any]] = []
    if isinstance(delivery_charge, (int, float)) and delivery_charge > 0:
        base_charges.append(
            {"amount": float(delivery_charge), "charge_type": "CHARGE_ID_DELIVERY_CHARGE"}
        )
    taxes_list: list[dict[str, Any]] = []
    if isinstance(taxes_amount, (int, float)) and taxes_amount > 0:
        taxes_list.append({"tax_amount": float(taxes_amount), "tax_type": "GST"})
    offers = cart_root.get("offers") if isinstance(cart_root.get("offers"), dict) else {}
    coupon = offers.get("coupon_applied")
    coupon_discount = offers.get("coupon_discount")

    # Payment method type — Swiggy exposes ``availablePaymentMethods``
    # at the envelope.data root (not inside cart_root). For now we
    # surface the first available method ("Cash on Delivery" is the
    # default Swiggy MCP returns through our config).
    payment_method = None
    parent = envelope.get("data")
    if isinstance(parent, dict):
        methods = parent.get("availablePaymentMethods")
        if isinstance(methods, list) and methods:
            first = methods[0]
            if isinstance(first, str) and first.strip():
                payment_method = first.strip()

    # Swiggy's ``deliverySubtitle`` looks like "35-40 mins to <address>";
    # split off the leading ETA chunk for the cart-card's eta chip.
    eta = None
    rest_blob = cart_root.get("restaurant")
    if isinstance(rest_blob, dict):
        subtitle = rest_blob.get("deliverySubtitle") or rest_blob.get("delivery_subtitle")
        if isinstance(subtitle, str) and " to " in subtitle:
            eta_part = subtitle.split(" to ", 1)[0].strip()
            if eta_part:
                eta = eta_part
        elif isinstance(subtitle, str) and subtitle.strip():
            eta = subtitle.strip()

    snapshot: dict[str, Any] = {
        "cart_id": str(cart_id).strip() if cart_id else None,
        "res_id": str(res_id_raw).strip() if res_id_raw is not None else None,
        "restaurant_name": (
            str(restaurant_name_raw).strip()
            if isinstance(restaurant_name_raw, str) and restaurant_name_raw.strip()
            else None
        ),
        "items": items,
        "item_total": item_total if isinstance(item_total, (int, float)) else None,
        "final_amount": to_pay if isinstance(to_pay, (int, float)) else None,
        "payment_method_type": payment_method,
        "eta": eta,
    }
    if base_charges or taxes_list:
        snapshot["charge_breakdown"] = {
            "base_charges": base_charges,
            "taxes": taxes_list,
        }
    if isinstance(coupon, str) and coupon.strip():
        snapshot["promo_code"] = coupon.strip()
    if isinstance(coupon_discount, (int, float)) and coupon_discount > 0:
        snapshot["promo_discount_amount"] = float(coupon_discount)
    return snapshot


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
    user_geo: dict[str, Any] | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
    session_cart: dict[str, Any] | None = None,
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

    # Per-plan menu index: ``{menu_item_id -> entry}`` populated from
    # every menu-producing step's response. Surfaced into
    # ``diagnostics["session_menu"]`` at end-of-turn so the next-turn
    # planner can emit literal ``menu_item_id`` + the right
    # ``variantsV2`` / ``addons`` rows without re-running ``search_menu``.
    # No cart-time matching happens here — mirrors zomato's pattern.
    menu_index: dict[str, dict[str, Any]] = {}
    # Seed from carry-forward so the LRU keeps items the user saw in
    # earlier turns even when this turn's plan doesn't re-fetch the menu.
    seed_menu: list[dict[str, Any]] = []
    if isinstance(session_menu, list):
        for entry in session_menu:
            if isinstance(entry, dict):
                seed_menu.append(entry)

    # Last-known cart snapshot: seeded from prior turn so the response
    # LLM can still narrate cart state on turns where no cart tool ran.
    # Replaced whole on every successful ``update_food_cart`` /
    # ``get_food_cart`` (Swiggy carts are single-restaurant, just like
    # Zomato's). Mirrors zomato.session_cart contract.
    cart_snapshot: dict[str, Any] | None = (
        session_cart if isinstance(session_cart, dict) else None
    )

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
                nid = _find_best_address_id_swiggy(envelope, location_text=location_text, user_geo=user_geo)
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

                # Defensive cart scrub — mirrors zomato._scrub_cart_items_for_zomato.
                # The planner is responsible for emitting the strict
                # request shape Swiggy MCP expects (snake_case
                # ``menu_item_id``, flat ``variantsV2: [{group_id,
                # variation_id}]``, flat ``addons: [{group_id,
                # choice_id}]``); we only drop rows where the
                # ``menu_item_id`` is obviously bogus (leftover
                # placeholder, blank). Everything else passes through.
                if tool == "update_food_cart" and isinstance(exec_args, dict):
                    exec_args, _dropped_rows = _scrub_swiggy_cart_items(exec_args)
                    if _dropped_rows:
                        logger.warning(
                            "update_food_cart: dropped %d row(s) with invalid menu_item_id: %s",
                            len(_dropped_rows),
                            _dropped_rows,
                        )
                        diagnostics.setdefault(
                            "update_food_cart_dropped_rows", []
                        ).extend(_dropped_rows)

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
                    # Harvest every menu item the user has been shown into
                    # the per-turn ``menu_index``, scoped to the active
                    # restaurant so cross-outlet items stay disambiguated.
                    # Surfaced as ``diagnostics["session_menu"]`` at
                    # end-of-turn for the next-turn planner. No code-side
                    # cart matching happens — mirrors zomato pattern.
                    active_rid = (
                        placeholder_values.get("__RESTAURANT_ID__") or ""
                    ).strip() or None
                    try:
                        fresh_idx = _collect_swiggy_menu_index_from_envelope(
                            envelope, res_id=active_rid
                        )
                    except Exception as e:
                        logger.info("swiggy menu index extraction failed: %s", e)
                        fresh_idx = {}
                    if fresh_idx:
                        for k, v in fresh_idx.items():
                            menu_index[k] = v

                if tool in _CART_PRODUCING_TOOLS and not envelope.get("is_error"):
                    # Capture the cart Swiggy actually built. Mirrors
                    # zomato.session_cart contract — sole ground-truth for
                    # "what's in the user's cart right now" so the response
                    # LLM doesn't have to infer it from chat history.
                    #
                    # Swiggy doesn't echo the restaurant name back in the
                    # cart envelope, so we hand it the active rid + the
                    # remembered restaurant entry (LRU) so the snapshot —
                    # and the chat-side cart card — show "Pizza Hut" in
                    # the header instead of "Restaurant".
                    active_rid_for_cart = (
                        placeholder_values.get("__RESTAURANT_ID__") or ""
                    ).strip() or None
                    active_rname: str | None = None
                    if active_rid_for_cart:
                        for entry in recent_restaurants:
                            if (
                                isinstance(entry, dict)
                                and str(entry.get("res_id") or "").strip() == active_rid_for_cart
                            ):
                                name_val = entry.get("name")
                                if isinstance(name_val, str) and name_val.strip():
                                    active_rname = name_val.strip()
                                    break
                    try:
                        snap = _extract_swiggy_cart_snapshot(
                            envelope,
                            restaurant_name_hint=active_rname,
                            res_id_hint=active_rid_for_cart,
                        )
                    except Exception as e:
                        logger.info("swiggy cart snapshot extraction failed: %s", e)
                        snap = None
                    if snap:
                        cart_snapshot = snap
                        diagnostics["session_cart"] = snap
                        diagnostics["fresh_cart_captured"] = True

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

    # Merge this turn's harvested menu rows with the seed (LRU) so the
    # next turn's planner sees everything the user has been shown across
    # the chat, not just this turn's slice. Mirrors zomato.session_menu.
    active_rid_final = (
        placeholder_values.get("__RESTAURANT_ID__") or ""
    ).strip() or None
    fresh_session_menu = _swiggy_menu_index_to_session_entries(
        menu_index, active_rid_final
    )
    merged_menu = _merge_swiggy_session_menu(seed_menu, fresh_session_menu)
    if merged_menu:
        diagnostics["session_menu"] = merged_menu

    # Echo the (possibly seeded) cart snapshot back so a turn that never
    # touched the cart still keeps the chat-side cart card alive.
    if cart_snapshot and not diagnostics.get("session_cart"):
        diagnostics["session_cart"] = cart_snapshot

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
    user_geo: dict[str, Any] | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
    session_cart: dict[str, Any] | None = None,
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
                user_geo=user_geo,
                session_placeholders=session_placeholders,
                session_restaurants=session_restaurants,
                session_addresses=session_addresses,
                session_menu=session_menu,
                session_cart=session_cart,
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
