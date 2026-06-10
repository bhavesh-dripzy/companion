"""Zomato planner session-context renderer (clean port of `_format_routing_message`).

`core/pipeline/stages/plan.py` `_generic_plan` calls `build_context(ctx)` and PREPENDS
the returned string to the planner input. The transcript, current user message and the
`(resolved address_id: ...)` line are added by `_format_input`, so this module renders
ONLY the session-state BRIEF blocks the planner reuses to avoid re-searching:

  * carried-forward placeholders   (from `ctx.session.placeholders`)
  * restaurants already shown       (`legacy["session_restaurants"]`)
  * saved delivery addresses        (`legacy["session_addresses"]`, selected marked)
  * menu items already shown        (`legacy["session_menu"]`, Zomato shape)
  * pending variant choice          (`ctx.session.pending_variant_choice`)

Headers, caps and row shapes are a faithful, verbatim parity port of the legacy
`app/services/agents/pipeline.py::_format_routing_message` blocks for these keys.
Every block is guarded so it renders only when its session slice is non-empty, and
each row defensively skips non-dict entries / rows missing their required id.
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.planner_context import placeholders_block, rows_for_provider

_PROVIDER_ID = "zomato"


def _rows(value: Any) -> list[dict[str, Any]]:
    """This provider's rows of a shared session list (foreign-platform ids hidden)."""
    return rows_for_provider(value, _PROVIDER_ID)


def _restaurants_block(restaurants: list[dict[str, Any]]) -> str | None:
    # Cap how many we surface to the planner — too long bloats every prompt.
    # 10 is the same order of magnitude as a typical user-visible result list.
    rows: list[str] = []
    for entry in restaurants[:10]:
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
        "instead of running get_restaurants_for_keyword again "
        "(Zomato's keyword search is fuzzy and often returns the wrong "
        "restaurant for short brand names):\n" + "\n".join(rows)
    )


def _addresses_block(
    addresses: list[dict[str, Any]], selected_address_id: str | None
) -> str | None:
    # Surface the user's saved delivery addresses so the planner can disambiguate
    # "use my work address" without re-fetching. Mark the currently-selected one
    # so the planner doesn't propose changing addresses mid-flow.
    sel = (selected_address_id or "").strip()
    rows: list[str] = []
    for entry in addresses[:8]:
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
        rows.append(f"{marker}{label} → address_id={aid}")
    if not rows:
        return None
    return (
        "Saved delivery addresses available this session (executor "
        "auto-uses the first / starred one for `__ADDRESS_ID__`; "
        "switch ONLY when the user explicitly asks to change "
        "address, then put the chosen literal address_id in your "
        "tool args):\n" + "\n".join(rows)
    )


def _variant_tail(entry: dict[str, Any]) -> str | None:
    variants = entry.get("variants") or []
    if not isinstance(variants, list) or not variants:
        return None
    names: list[str] = []
    for v in variants[:6]:
        if not isinstance(v, dict):
            continue
        pv = v.get("property_values") or {}
        if isinstance(pv, dict) and pv:
            names.append(
                "/".join(f"{k}={vv}" for k, vv in pv.items() if vv)
                or str(v.get("variant_id") or "")
            )
    return f"variants: {', '.join(names)}" if names else None


def _addon_groups_tail(entry: dict[str, Any]) -> str | None:
    groups = entry.get("addon_groups") or []
    if not isinstance(groups, list) or not groups:
        return None
    gtags: list[str] = []
    for g in groups[:4]:
        if not isinstance(g, dict):
            continue
        gname = str(g.get("name") or g.get("id") or "").strip()
        mn = int(g.get("min") or 0)
        mx = int(g.get("max") or 0)
        if gname:
            gtags.append(f"{gname} (min={mn}, max={mx})")
    return "addon_groups: " + "; ".join(gtags) if gtags else None


def _menu_row(entry: dict[str, Any]) -> str | None:
    vid = str(entry.get("variant_id") or "").strip()
    iname = str(entry.get("item_name") or "").strip()
    if not vid or not iname:
        return None
    price_raw = entry.get("discounted_price") or entry.get("price")
    price = f" — ₹{price_raw}" if price_raw not in (None, "", 0, "0") else ""
    tail_bits: list[str] = []
    rid = str(entry.get("res_id") or "").strip()
    if rid:
        tail_bits.append(f"res_id={rid}")
    variant_tail = _variant_tail(entry)
    if variant_tail:
        tail_bits.append(variant_tail)
    addon_tail = _addon_groups_tail(entry)
    if addon_tail:
        tail_bits.append(addon_tail)
    tail = f" [{' | '.join(tail_bits)}]" if tail_bits else ""
    return f"  - {iname} → variant_id={vid}{price}{tail}"


def _menu_block(menu: list[dict[str, Any]]) -> str | None:
    # Surface the recently-shown menu items so the planner can emit `create_cart`
    # directly when the user picks one (no redundant menu fetch).
    rows: list[str] = []
    for entry in menu[:25]:
        row = _menu_row(entry)
        if row:
            rows.append(row)
    if not rows:
        return None
    return (
        "Menu items already shown to the user this session — when the "
        "user picks one by name, put the literal variant_id below into "
        "your `create_cart` `items[].variant_id` (no need to re-run "
        "menu/search tools). If the row lists `variants:`, pick the "
        "variant whose `property_values` match the user's words "
        "(size/crust/etc.). If `addon_groups:` are listed with min>=1, "
        "you can include explicit picks in items[].add_ons when the "
        "user names them.\n" + "\n".join(rows)
    )


def _pending_variant_block(pending: list[dict[str, Any]]) -> str | None:
    # Surface the variant menu the previous turn deferred so the planner can emit
    # literal groupId / variation.id values when the user replies with their pick.
    blocks: list[str] = []
    for item in pending[:3]:
        if not isinstance(item, dict):
            continue
        iname = str(item.get("item_name") or "").strip() or "(unnamed item)"
        iid = str(item.get("item_id") or "").strip()
        blocks.append(f"  - {iname} (item_id={iid}):" if iid else f"  - {iname}:")
        for group in (item.get("groups") or [])[:4]:
            if not isinstance(group, dict):
                continue
            gname = str(group.get("name") or "Choose").strip()
            gid = str(group.get("groupId") or "").strip()
            blocks.append(f"      group {gname!r} (group_id={gid}):")
            for choice in (group.get("choices") or [])[:12]:
                if not isinstance(choice, dict):
                    continue
                cname = str(choice.get("name") or "").strip()
                cid = str(choice.get("id") or "").strip()
                price = choice.get("price")
                price_tail = f" ₹{price}" if price not in (None, "") else ""
                blocks.append(f"        * {cname} → variation_id={cid}{price_tail}")
    if not blocks:
        return None
    return (
        "Pending variant choice from the previous turn — the user is "
        "replying with their pick now. Build a fresh `update_food_cart` "
        "with literal `cartItems[].variantsV2 = [{groupId: <group_id>, "
        "variations: [{id: <variation_id>}]}]` using the ids below. "
        "Pick one variation per group matching the user's words "
        '("medium" → Size group\'s Medium variation, etc.). '
        "Never emit `__VARIANT_ID__` / `__GROUP_ID__` placeholders — "
        "they don't exist.\n" + "\n".join(blocks)
    )


def build_context(ctx: Any) -> str:
    """Render the Zomato session-state brief blocks present in ``ctx.session``.

    Returns the rendered blocks joined by blank lines, or ``""`` when none apply.
    Block order mirrors the legacy `_format_routing_message`: placeholders →
    restaurants → addresses → menu → pending variant choice.
    """
    session = ctx.session
    legacy = session.legacy if isinstance(session.legacy, dict) else {}
    placeholders = session.placeholders if isinstance(session.placeholders, dict) else {}
    pending = (
        session.pending_variant_choice if isinstance(session.pending_variant_choice, list) else []
    )

    blocks = [
        placeholders_block(placeholders, _PROVIDER_ID),
        _restaurants_block(_rows(legacy.get("session_restaurants"))),
        _addresses_block(_rows(legacy.get("session_addresses")), session.selected_address_id),
        _menu_block(_rows(legacy.get("session_menu"))),
        _pending_variant_block(pending),
    ]
    return "\n\n".join(block for block in blocks if block)
