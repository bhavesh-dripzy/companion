"""Swiggy Food planner session-context renderer (clean port).

Faithful parity port of the Swiggy session blocks from the legacy
``agents.pipeline._format_routing_message`` (placeholders / restaurants /
addresses / menu / pending-variant-choice). Renders ONLY the session-state
brief prepended to the planner input; the transcript, current user message,
and ``(resolved address_id: ...)`` line are added by ``plan._format_input``
and must NOT be repeated. ``session_cart`` is executor-seed-only and was never
surfaced to the legacy planner, so neither is it here. Reads typed
``ctx.session`` (``core/contracts/session.py``): ``legacy`` flat bag
(``session_restaurants`` / ``session_addresses`` / ``session_menu``),
``placeholders``, ``selected_address_id``, ``pending_variant_choice``.
Headers/caps/row shapes are verbatim from legacy. Stdlib + typing only.
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.planner_context import placeholders_block, rows_for_provider

_PROVIDER_ID = "swiggy_food"


def _rows(value: Any, cap: int) -> list[Any]:
    """Capped list of the value (``[]`` for non-lists) — defensive iteration."""
    return value[:cap] if isinstance(value, list) else []


def _restaurants_block(session_restaurants: Any) -> str | None:
    rows: list[str] = []
    for entry in _rows(session_restaurants, 10):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        rid = str(entry.get("res_id") or "").strip()
        if not name or not rid:
            continue
        rating = entry.get("rating")
        tail = f" (rating {rating})" if rating not in (None, "") else ""
        rows.append(f"  - {name} → res_id={rid}{tail}")
    if not rows:
        return None
    return (
        "Restaurants already shown to the user this session — if the "
        "user picks one of these by name, use the literal res_id below "
        "instead of running search_restaurants again (keyword search is "
        "fuzzy and often returns the wrong restaurant for short brand "
        "names):\n" + "\n".join(rows)
    )


def _addresses_block(session_addresses: Any, selected_address_id: str | None) -> str | None:
    sel = (selected_address_id or "").strip()
    addr_rows: list[str] = []
    for entry in _rows(session_addresses, 8):
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("address_id") or "").strip()
        if not aid:
            continue
        label = str(
            entry.get("label")
            or entry.get("location_name")
            or entry.get("addressTag")
            or entry.get("address_tag")
            or entry.get("address_line")
            or aid
        ).strip()
        marker = "  * " if aid == sel else "  - "
        addr_rows.append(f"{marker}{label} → address_id={aid}")
    if not addr_rows:
        return None
    return (
        "Saved delivery addresses available this session (executor "
        "auto-uses the first / starred one for `__ADDRESS_ID__`; "
        "switch ONLY when the user explicitly asks to change "
        "address, then put the chosen literal address_id in your "
        "tool args):\n" + "\n".join(addr_rows)
    )


def _menu_item_tail(entry: dict[str, Any], rid: str) -> str:
    """Render the ``[res_id=… | variants:… | variantsV2:… | addons:…]`` id tail."""
    tail_bits: list[str] = [f"res_id={rid}"] if rid else []
    names: list[str] = []  # Zomato shape: flattened variants[] + addon_groups[].
    for v in _rows(entry.get("variants"), 6):
        if not isinstance(v, dict):
            continue
        pv = v.get("property_values") or {}
        if isinstance(pv, dict) and pv:
            names.append(
                "/".join(f"{k}={vv}" for k, vv in pv.items() if vv)
                or str(v.get("variant_id") or "")
            )
    if names:
        tail_bits.append(f"variants: {', '.join(names)}")
    gtags: list[str] = []
    for g in _rows(entry.get("addon_groups"), 4):
        if not isinstance(g, dict):
            continue
        gname = str(g.get("name") or g.get("id") or "").strip()
        if gname:
            gtags.append(f"{gname} (min={int(g.get('min') or 0)}, max={int(g.get('max') or 0)})")
    if gtags:
        tail_bits.append("addon_groups: " + "; ".join(gtags))
    group_tags: list[str] = []  # Swiggy shape: response-shape variantsV2 + addons.
    for grp in _rows(entry.get("variantsV2"), 4):
        if not isinstance(grp, dict):
            continue
        gid = str(grp.get("groupId") or grp.get("group_id") or "").strip()
        gname = str(grp.get("name") or "").strip() or gid
        choices: list[str] = []
        for vrow in _rows(grp.get("variations"), 6):
            if not isinstance(vrow, dict) or vrow.get("inStock", 1) not in (1, True):
                continue
            vid2 = str(vrow.get("id") or "").strip()
            if not vid2:
                continue
            choices.append(f"{str(vrow.get('name') or '').strip() or vid2}={gid}/{vid2}")
        if choices:
            group_tags.append(f"{gname}({','.join(choices)})")
    if group_tags:
        tail_bits.append("variantsV2: " + " | ".join(group_tags))
    a_tags: list[str] = []
    for grp in _rows(entry.get("addons"), 4):
        if not isinstance(grp, dict):
            continue
        gid = str(grp.get("groupId") or grp.get("group_id") or "").strip()
        gname = str(grp.get("name") or "").strip() or gid
        mn = grp.get("minAddons") or grp.get("min_addons") or grp.get("min") or 0
        mx = grp.get("maxAddons") or grp.get("max_addons") or grp.get("max") or 0
        achoices: list[str] = []
        for ch in _rows(grp.get("choices") or grp.get("addonChoices"), 5):
            if not isinstance(ch, dict):
                continue
            cid = str(ch.get("id") or ch.get("choice_id") or "").strip()
            if cid:
                achoices.append(f"{str(ch.get('name') or '').strip() or cid}={gid}/{cid}")
        cstr = (", ".join(achoices)) if achoices else ""
        a_tags.append(f"{gname} (min={mn}, max={mx}){' → ' + cstr if cstr else ''}")
    if a_tags:
        tail_bits.append("addons: " + " | ".join(a_tags))
    return f" [{' | '.join(tail_bits)}]" if tail_bits else ""


def _menu_block(session_menu: Any) -> str | None:
    menu_rows: list[str] = []
    for entry in _rows(session_menu, 25):
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("variant_id") or "").strip()
        iname = str(entry.get("item_name") or "").strip()
        if not vid or not iname:
            continue
        rid = str(entry.get("res_id") or "").strip()
        price_raw = entry.get("discounted_price") or entry.get("price")
        price = f" — ₹{price_raw}" if price_raw not in (None, "", 0, "0") else ""
        tail = _menu_item_tail(entry, rid)
        is_swiggy = bool(
            entry.get("variantsV2")
            or entry.get("hasVariants") is not None
            or entry.get("hasAddons") is not None
            or entry.get("menu_item_id")
        )
        id_label = "menu_item_id" if is_swiggy else "variant_id"
        menu_rows.append(f"  - {iname} → {id_label}={vid}{price}{tail}")
    if not menu_rows:
        return None
    return (
        "Menu items already shown to the user this session — when the "
        "user picks one by name, put the literal id below into your "
        "cart-add step (no need to re-run menu/search tools).\n"
        "- **Zomato** rows use `variant_id=…` → paste into `create_cart` "
        "`items[].variant_id`. If the row lists `variants:`, pick the "
        "variant whose `property_values` match the user's words "
        "(size/crust/etc.). If `addon_groups:` are listed with min>=1, "
        "you can include explicit picks in items[].add_ons when the "
        "user names them.\n"
        "- **Swiggy** rows use `menu_item_id=…` → paste into "
        "`update_food_cart` `cartItems[].menu_item_id`. The `variantsV2:`"
        " tail lists each group as "
        "`GroupName(Variation=group_id/variation_id, …)` — pick ONE "
        "variation per group and emit "
        "`cartItems[].variantsV2: [{group_id, variation_id}]` for each "
        "chosen pair. The `addons:` tail uses the same "
        "`group_id/choice_id` convention — emit "
        "`cartItems[].addons: [{group_id, choice_id}]` for every required "
        "addon (min>=1) and any optional ones the user named. Always "
        "include the literal `restaurantId` from `Restaurants already "
        "shown` for the item's outlet.\n" + "\n".join(menu_rows)
    )


def _pending_variant_block(pending_variant_choice: Any) -> str | None:
    pvc_blocks: list[str] = []
    for item in _rows(pending_variant_choice, 3):
        if not isinstance(item, dict):
            continue
        iname = str(item.get("item_name") or "").strip() or "(unnamed item)"
        iid = str(item.get("item_id") or "").strip()
        pvc_blocks.append(f"  - {iname} (item_id={iid}):" if iid else f"  - {iname}:")
        for group in _rows(item.get("groups"), 4):
            if not isinstance(group, dict):
                continue
            gname = str(group.get("name") or "Choose").strip()
            gid = str(group.get("groupId") or "").strip()
            pvc_blocks.append(f"      group {gname!r} (group_id={gid}):")
            for choice in _rows(group.get("choices"), 12):
                if not isinstance(choice, dict):
                    continue
                cname = str(choice.get("name") or "").strip()
                cid = str(choice.get("id") or "").strip()
                price = choice.get("price")
                price_tail = f" ₹{price}" if price not in (None, "") else ""
                pvc_blocks.append(f"        * {cname} → variation_id={cid}{price_tail}")
    if not pvc_blocks:
        return None
    return (
        "Pending variant choice from the previous turn — the user is "
        "replying with their pick now. Build a fresh `update_food_cart` "
        "with literal `cartItems[].variantsV2 = [{groupId: <group_id>, "
        "variations: [{id: <variation_id>}]}]` using the ids below. "
        "Pick one variation per group matching the user's words "
        '("medium" → Size group\'s Medium variation, etc.). '
        "Never emit `__VARIANT_ID__` / `__GROUP_ID__` placeholders — "
        "they don't exist.\n" + "\n".join(pvc_blocks)
    )


def build_context(ctx: Any) -> str:
    """Render the Swiggy Food session brief prepended to the planner input."""
    session = ctx.session
    legacy = session.legacy if isinstance(session.legacy, dict) else {}
    parts: list[str | None] = [
        placeholders_block(session.placeholders, _PROVIDER_ID),
        _restaurants_block(rows_for_provider(legacy.get("session_restaurants"), _PROVIDER_ID)),
        _addresses_block(
            rows_for_provider(legacy.get("session_addresses"), _PROVIDER_ID),
            session.selected_address_id,
        ),
        _menu_block(rows_for_provider(legacy.get("session_menu"), _PROVIDER_ID)),
        _pending_variant_block(session.pending_variant_choice),
    ]
    return "\n\n".join(block for block in parts if block)
