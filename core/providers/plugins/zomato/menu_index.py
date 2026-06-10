"""Zomato `session_menu` (menu_index) — the variant→item index the planner reuses.

Clean port of the executor's `_collect_menu_index_from_{envelope,keyword_envelope}`
+ `_merge_session_menu`, adapted to operate on `ToolResult`s. The planner reads
`session_menu` to pick a literal `variant_id` for a follow-up `create_cart`
("add the garlic bread") without re-fetching the menu.

Scope: the common cart-by-name path is faithful (variant_id, item_name, price,
discounted_price, categories, item_id, res_id) and raw `variants` / `available_add_ons`
refs are passed through verbatim. The executor's cross-referenced addon-group *table
expansion* (joining `all_addons_data` to expand group ids into full schemas) is the
one piece deferred — complex-customization follow-ups re-fetch the menu, exactly as
on a cold start. Validated against the real menu fixture.
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool_result import ToolResult
from core.providers.mcp.lru import backfill_fields, scoped_lru_merge

_MAX_MENU_ITEMS = 35
_MENU_TOOLS = {"get_menu_items_listing", "get_restaurant_menu_by_categories"}
_KEYWORD_TOOLS = {"get_restaurants_for_keyword"}
_MAX_KEYWORD_RESTAURANTS = 8
_KEYWORD_ITEMS_PER_RESTAURANT = 3


def _inner(envelope: dict[str, Any]) -> dict[str, Any]:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    inner = result.get("data") if isinstance(result.get("data"), dict) else {}
    return inner if isinstance(inner, dict) else {}


def _entry_from_row(row: dict[str, Any], res_id: str | None) -> dict[str, Any] | None:
    vid = str(row.get("variant_id") or row.get("variantId") or "").strip()
    name = row.get("item_name") or row.get("itemName") or row.get("name") or row.get("title")
    if not vid or not isinstance(name, str) or not name.strip():
        return None
    entry: dict[str, Any] = {
        "variant_id": vid,
        "item_name": name.strip(),
        "price": row.get("price"),
        "discounted_price": row.get("discounted_price"),
        "categories": row.get("categories"),
        "variants": row.get("variants") if isinstance(row.get("variants"), list) else [],
        "available_add_ons": row.get("available_add_ons")
        if isinstance(row.get("available_add_ons"), list)
        else [],
    }
    item_id = row.get("item_id") or row.get("itemId")
    if isinstance(item_id, str) and item_id.strip():
        entry["item_id"] = item_id.strip()
    if res_id:
        entry["res_id"] = res_id
    return entry


def menu_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """variant→entry rows from a menu envelope (item_mappings + menu_items/partial_menu)."""
    inner = _inner(envelope)
    res_id = str(inner.get("res_id") or "").strip() or None
    rows: list[Any] = []
    for key in ("item_mappings", "menu_items", "partial_menu"):
        val = inner.get(key)
        if isinstance(val, list):
            rows.extend(r for r in val if isinstance(r, dict))
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = _entry_from_row(row, res_id)
        if entry is None:
            continue
        out[entry["variant_id"]] = _richer(out.get(entry["variant_id"]), entry)
    return list(out.values())


def keyword_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Featured per-restaurant items from a keyword-search envelope (res_id stamped)."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    results = data.get("results")
    if not isinstance(results, list):
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        results = inner.get("results") if isinstance(inner.get("results"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for resto in results[:_MAX_KEYWORD_RESTAURANTS]:
        if not isinstance(resto, dict):
            continue
        rid = str(resto.get("res_id") or resto.get("restaurant_id") or "").strip()
        if not rid:
            continue
        items = resto.get("items") if isinstance(resto.get("items"), list) else []
        kept = 0
        for it in items:
            entry = _entry_from_row(it, rid) if isinstance(it, dict) else None
            if entry is None or entry["variant_id"] in out:
                continue
            # keyword items carry catalogue_id, not item_id
            cat_id = it.get("catalogue_id")
            if "item_id" not in entry and isinstance(cat_id, str) and cat_id.strip():
                entry["item_id"] = cat_id.strip()
            out[entry["variant_id"]] = entry
            kept += 1
            if kept >= _KEYWORD_ITEMS_PER_RESTAURANT:
                break
    return list(out.values())


def _richer(prev: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Prefer the entry carrying more customization detail; refresh scalar prices."""
    if prev is None:
        return new
    prev_score = len(prev.get("variants") or []) + len(prev.get("available_add_ons") or [])
    new_score = len(new.get("variants") or []) + len(new.get("available_add_ons") or [])
    winner, loser = (new, prev) if new_score >= prev_score else (prev, new)
    for key in ("price", "discounted_price"):
        if winner.get(key) is None and loser.get(key) is not None:
            winner[key] = loser[key]
    return winner


def _key(entry: dict[str, Any]) -> str:
    """Dedup on the stable item_id (ctl_*) when present — Zomato re-mints
    variant_ids per fetch, so keying on variant_id keeps duplicate rows whose
    stale variant_id Zomato then rejects at create_cart. Fresh-first merge order
    means the row carrying the CURRENT variant_id wins."""
    rid = str(entry.get("res_id") or "").strip()
    item = str(entry.get("item_id") or "").strip()
    return f"{rid}|{item or entry.get('variant_id')}".lower()


def merge_menu(
    prior: list[dict[str, Any]] | None, results: list[ToolResult]
) -> list[dict[str, Any]]:
    """Most-recent-first session_menu LRU from this turn's menu + keyword envelopes."""
    fresh: list[dict[str, Any]] = []
    for tr in results:
        if not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        if tr.tool in _MENU_TOOLS:
            fresh.extend(menu_entries(envelope))
        elif tr.tool in _KEYWORD_TOOLS:
            fresh.extend(keyword_entries(envelope))
    if not fresh and not prior:
        return []
    return scoped_lru_merge(
        prior,
        fresh,
        provider="zomato",
        key=_key,
        cap=_MAX_MENU_ITEMS,
        combine=backfill_fields(
            "price", "discounted_price", "categories", "variants", "available_add_ons"
        ),
    )
