"""Swiggy Food cart upsert/reconcile (port of Instamart's REPLACE-ALL → UPSERT fix).

`update_food_cart` REPLACES the whole cart server-side — verified live: adding only
item B after item A left the cart with B alone. So a planner that lists only the
changed item silently wipes the rest. We fold the planner's items onto the cart's
CURRENT lines (read live in `before_run`) keyed by `menu_item_id`, rebuilding each
existing line into the strict request shape
``{menu_item_id, quantity, variantsV2:[{group_id, variation_id}], addons:[{group_id, choice_id}]}``.

The cart envelope carries each item's full ``variants``/``addons`` (response shape)
but NOT a ``restaurantId`` — so existing lines are rebuilt from the live envelope
(the session snapshot is lossy), and restaurant-switch detection uses the carried
``session_cart.res_id`` instead.
"""

from __future__ import annotations

from typing import Any

from core.providers.plugins.swiggy_food.cart_cards import _cart_root


def _variant_rows(item: dict[str, Any]) -> list[dict[str, str]]:
    """Existing item ``variants`` (response shape) → request ``variantsV2`` rows."""
    out: list[dict[str, str]] = []
    for v in item.get("variants") or []:
        if not isinstance(v, dict):
            continue
        gid = str(v.get("group_id") or v.get("groupId") or "").strip()
        vid = str(v.get("variation_id") or v.get("variationId") or v.get("id") or "").strip()
        if gid and vid:
            out.append({"group_id": gid, "variation_id": vid})
    return out


def _addon_rows(item: dict[str, Any]) -> list[dict[str, str]]:
    """Existing item ``addons`` (response shape) → request ``addons`` rows."""
    out: list[dict[str, str]] = []
    for a in item.get("addons") or []:
        if not isinstance(a, dict):
            continue
        gid = str(a.get("group_id") or a.get("groupId") or "").strip()
        cid = str(a.get("choice_id") or a.get("choiceId") or a.get("id") or "").strip()
        if gid and cid:
            out.append({"group_id": gid, "choice_id": cid})
    return out


def request_rows_from_cart(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """The cart's current lines as ``update_food_cart`` request rows, ready to
    re-send. Returns ``[]`` for an empty/missing/error cart."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return []
    root = _cart_root(envelope.get("data"))
    if not root:
        return []
    rows: list[dict[str, Any]] = []
    items = root.get("items") or root.get("cartItems") or root.get("cart_items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("menu_item_id") or it.get("menuItemId") or it.get("itemId") or "").strip()
        if not iid:
            continue
        try:
            qty = int(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            continue
        row: dict[str, Any] = {"menu_item_id": iid, "quantity": qty}
        v2 = _variant_rows(it)
        if v2:
            row["variantsV2"] = v2
        addons = _addon_rows(it)
        if addons:
            row["addons"] = addons
        rows.append(row)
    return rows


def _variant_index(variants_v2: list[Any]) -> dict[str, set[str]]:
    """{group_id -> {valid variation_ids}} from a menu envelope's variantsV2
    (response shape: ``[{groupId, variations:[{id}]}]``)."""
    out: dict[str, set[str]] = {}
    for g in variants_v2 or []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("groupId") or g.get("group_id") or "").strip()
        if not gid:
            continue
        vids: set[str] = set()
        for v in g.get("variations") or []:
            if isinstance(v, dict):
                vid = str(
                    v.get("id") or v.get("variation_id") or v.get("variationId") or ""
                ).strip()
                if vid:
                    vids.add(vid)
        out[gid] = vids
    return out


def variant_rows_valid(rows: Any, variants_v2: list[Any]) -> bool:
    """True when EVERY request row ``{group_id, variation_id}`` names a real group +
    a real variation in that group (per the menu index).

    Catches the planner mixing up the fields — e.g. Sarvam emitting
    ``{group_id: "Crust", variation_id: <a group id>}`` — which Swiggy 500s on."""
    if not isinstance(rows, list) or not rows:
        return False
    index = _variant_index(variants_v2)
    if not index:
        return True  # no menu to validate against — don't second-guess the planner
    for r in rows:
        if not isinstance(r, dict):
            return False
        gid = str(r.get("group_id") or r.get("groupId") or "").strip()
        vid = str(r.get("variation_id") or r.get("variationId") or "").strip()
        if gid not in index or vid not in index[gid]:
            return False
    return True


def reconcile_cart_items(
    args: dict[str, Any],
    *,
    existing_rows: list[dict[str, Any]],
    valid_ids: set[str],
    fold: bool,
) -> None:
    """Fold the planner's ``cartItems`` onto the cart's existing lines (in place).

    * ``fold`` False (cart cleared this plan, or a restaurant SWITCH with new items)
      → rebuild from the planner's items only.
    * Removal is by OMISSION — a planner qty<=0 drops the line (Swiggy errors on a
      literal qty 0 and it can poison the cart), never sent.
    * A ``menu_item_id`` the planner invented (not from a real menu/search at this
      restaurant, and not already in the cart) is dropped — skipping it beats Swiggy
      rejecting the whole call with INVALID_ITEM_IDS_IN_REQUEST.
    * An existing line re-listed for a quantity change keeps its prior
      variantsV2/addons when the planner omits them.
    """
    merged: dict[str, dict[str, Any]] = {}
    if fold:
        for row in existing_rows:
            iid = str(row.get("menu_item_id") or "").strip()
            if iid:
                merged[iid] = dict(row)
    for it in args.get("cartItems") or []:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("menu_item_id") or "").strip()
        if not iid:
            continue
        try:
            qty = int(it.get("quantity"))
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            merged.pop(iid, None)
            continue
        prior = merged.get(iid) or {}
        if (
            valid_ids
            and iid not in valid_ids
            and iid not in {str(r.get("menu_item_id") or "").strip() for r in existing_rows}
        ):
            continue
        row: dict[str, Any] = {"menu_item_id": iid, "quantity": qty}
        v2 = it.get("variantsV2") or prior.get("variantsV2")
        if v2:
            row["variantsV2"] = v2
        addons = it.get("addons") or prior.get("addons")
        if addons:
            row["addons"] = addons
        merged[iid] = row
    args["cartItems"] = list(merged.values())
