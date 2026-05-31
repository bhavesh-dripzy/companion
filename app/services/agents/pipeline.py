"""Agent entry: orchestrator → domain food planner → MCP → response LLM."""

from __future__ import annotations

from typing import Any

from app.services.agents.orchestrator import classify_agent_routing
from app.services.agents.tool_trace import build_tool_execution_trace
from app.services.debug_trace import new_turn, snip, trace
from app.services.food_llm_router import route_food_query
from app.services.food_response_builder import (
    build_assistant_message_with_llm,
    build_zomato_summary,
)
from app.services.calendar import (
    execute_reminder_calendar,
    plan_reminder_calendar,
    reply_reminder_calendar,
)
from app.services.general_chat import plan_normal_query
from app.services.general_chat import responder as general_chat_responder
from app.services.llm_config import ResolvedLlm
from app.services.maps import (
    execute_maps_query,
    plan_maps_query,
    reply_maps_query,
)
from app.services.tool_runtime import execute_tool_plan_sync, get_tool_provider


class CustomMcpNeedsDiscoveryError(ValueError):
    """Raised when a routed custom MCP lacks a cached tool manifest."""


# Largest single attachment we'll surface to the chat UI in one turn. The
# Zomato QR PNG is ~5 KB, but inline base64 images grow as 1.33x the raw
# byte count and bloat history payloads; cap conservatively so a runaway
# provider can't tank the chat bubble.
_MAX_ATTACHMENT_DATA_B64_CHARS = 200_000  # ~150 KB raw

# Max cards we ship in a single menu_cards attachment. The Zomato executor
# already trims the visible menu envelope to ``_MENU_ITEMS_PER_RESTAURANT``
# (8) so the cards naturally match what the response LLM sees and what the
# next-turn planner has in ``session_menu``. This is a defensive upper
# bound in case an envelope ever returns more rows than the trimmer cap.
_MAX_MENU_CARDS = 25

# Tools whose Zomato envelopes carry rich menu_item rows we can render
# as cards (image / price / rating).
#   * get_restaurant_menu_by_categories → rich rows live in ``menu_items[]``
#   * get_menu_items_listing → rich rows live in ``partial_menu[]`` (the
#     listing tool returns 10 fully-detailed rows alongside the thin
#     ``item_mappings[]``; the silent by-categories enrichment merges
#     prices onto item_mappings, but partial_menu was already card-shaped
#     out of the box — name, image_url, price, description, rating, etc.).
# We card-render from whichever rich list the envelope carries, intersected
# with the visible variant_ids on item_mappings/menu_items so the cards on
# screen, the response LLM's text, and ``session_menu`` always agree.
_MENU_CARD_TOOLS = {
    "get_restaurant_menu_by_categories",
    "get_menu_items_listing",
}


def _collect_payment_attachments(raw_tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Lift any ``payment.qr_b64`` blocks out of raw tool steps.

    Walks every provider's executor output (currently only Zomato emits a
    ``payment`` block, but the contract is generic) and returns a flat list of
    attachment descriptors the frontend can render inline. The original step
    output is left untouched so downstream consumers (response-LLM, observable
    trace, debug payloads) see the same structure.

    Attachment shape (stable contract for ``out["attachments"]``)::

        {
          "kind": "image",
          "source": "zomato.checkout_cart" | "<provider>.<tool>",
          "mime_type": "image/png",
          "data_b64": "<base64 payload>",
          "alt": "Payment QR code",
          "caption": "Scan with any UPI app",
          # optional, only when the QR was decoded:
          "upi_link": "upi://pay?pa=...",
          "amount_inr": "185",
          "payee_vpa": "...",
          "payee_name": "...",
          "transaction_ref": "...",
        }
    """
    if not isinstance(raw_tool_results, dict):
        return []

    attachments: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            envelope = step.get("result")
            if not isinstance(envelope, dict):
                continue
            payment = envelope.get("payment")
            if not isinstance(payment, dict):
                continue
            data_b64 = payment.get("qr_b64")
            if not isinstance(data_b64, str) or not data_b64.strip():
                continue
            if len(data_b64) > _MAX_ATTACHMENT_DATA_B64_CHARS:
                # Refuse to inline absurdly large payloads — drop a breadcrumb
                # in the attachment so the UI can fall back to a link.
                attachment_oversize = {
                    "kind": "image",
                    "source": f"{provider_name}.{step.get('tool') or 'unknown'}",
                    "mime_type": payment.get("qr_mime_type") or "image/png",
                    "data_b64": None,
                    "oversize": True,
                    "alt": "Payment QR code",
                    "caption": "QR image too large to inline; open via UPI link.",
                    "upi_link": payment.get("upi_link"),
                    "amount_inr": payment.get("amount_inr"),
                    "payee_vpa": payment.get("payee_vpa"),
                    "payee_name": payment.get("payee_name"),
                    "transaction_ref": payment.get("transaction_ref"),
                }
                attachments.append(
                    {k: v for k, v in attachment_oversize.items() if v is not None}
                )
                continue

            amount = payment.get("amount_inr")
            payee = payment.get("payee_name") or payment.get("payee_vpa")
            caption_parts = ["Scan with any UPI app"]
            if amount:
                caption_parts.append(f"₹{amount}")
            if payee:
                caption_parts.append(f"to {payee}")
            caption = " — ".join(caption_parts)

            attachment: dict[str, Any] = {
                "kind": "image",
                "source": f"{provider_name}.{step.get('tool') or 'checkout_cart'}",
                "mime_type": payment.get("qr_mime_type") or "image/png",
                "data_b64": data_b64,
                "alt": "Payment QR code",
                "caption": caption,
            }
            for key in ("upi_link", "amount_inr", "payee_vpa", "payee_name", "transaction_ref"):
                val = payment.get(key)
                if val:
                    attachment[key] = val
            attachments.append(attachment)
    return attachments


def _derive_veg_kind(item_tags: Any) -> str | None:
    """Map Zomato's ``item_tags`` list onto a card-friendly veg flag.

    Returns ``"veg"`` / ``"non_veg"`` / ``"egg"`` so the frontend can paint the
    classic green / red / orange dot. Returns ``None`` when the tags don't
    name a food-class (rare — Zomato applies these consistently across
    Subway / McDonald's / WeFit / Domino's / KFC, verified live).
    """
    if not isinstance(item_tags, list):
        return None
    norm = {str(t).strip().lower() for t in item_tags if isinstance(t, str)}
    if not norm:
        return None
    if "non-veg" in norm or "nonveg" in norm:
        return "non_veg"
    if "egg" in norm or "egg-veg" in norm:
        return "egg"
    if "veg" in norm:
        return "veg"
    return None


def _build_menu_card(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise a Zomato ``menu_items[]`` row into a renderable card.

    Returns ``None`` for rows missing the minimum-viable fields (name +
    either ``item_id`` or ``variant_id``) so the grid never renders a
    blank tile. Everything else is best-effort — fields that aren't
    populated are simply omitted (the frontend treats them as missing).

    Field choices are deliberate:
      * ``image_url`` — only when Zomato returned a non-empty CDN URL.
        We don't synthesise placeholders here; the UI falls back to a
        veg-coloured tile when the field is absent.
      * ``rating`` / ``votes`` — Zomato returns ``0`` for "no rating yet";
        coerce those to ``None`` so the UI can hide the chip entirely
        instead of showing "★ 0".
      * ``price`` / ``discounted_price`` — both shipped; the UI shows
        the discounted price prominently with the original struck-through.
      * ``description`` — trimmed to 140 chars (a card line) at the
        source so we don't ship long-form prose just to truncate it client-side.
    """
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    item_id = item.get("item_id")
    variant_id = item.get("variant_id")
    if not (isinstance(item_id, str) and item_id.strip()) and not (
        isinstance(variant_id, str) and variant_id.strip()
    ):
        return None

    card: dict[str, Any] = {"name": name.strip()}
    if isinstance(item_id, str) and item_id.strip():
        card["item_id"] = item_id.strip()
    if isinstance(variant_id, str) and variant_id.strip():
        card["variant_id"] = variant_id.strip()

    price = item.get("price")
    if isinstance(price, (int, float)) and price > 0:
        card["price"] = price
    disc = item.get("discounted_price")
    if isinstance(disc, (int, float)) and disc > 0 and disc != price:
        card["discounted_price"] = disc

    image = item.get("image_url")
    if isinstance(image, str) and image.strip().startswith(("http://", "https://")):
        card["image_url"] = image.strip()

    rating = item.get("rating")
    if isinstance(rating, (int, float)) and rating > 0:
        card["rating"] = round(float(rating), 1)
    votes = item.get("reviews") or item.get("votes")
    if isinstance(votes, (int, float)) and votes > 0:
        card["votes"] = int(votes)

    description = item.get("description")
    if isinstance(description, str) and description.strip():
        desc = description.strip()
        card["description"] = (desc[:140] + "…") if len(desc) > 140 else desc

    veg_kind = _derive_veg_kind(item.get("item_tags"))
    if veg_kind:
        card["veg_kind"] = veg_kind

    cats = item.get("categories")
    if isinstance(cats, list):
        clean = [c.strip() for c in cats if isinstance(c, str) and c.strip()]
        if clean:
            card["category"] = clean[0]

    return card


def _collect_menu_attachments(raw_tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Lift Zomato menu_items[] out of raw tool steps into renderable cards.

    Walks ``raw_tool_results[<provider>].steps[]`` looking for menu-fetch
    tools (currently only ``get_restaurant_menu_by_categories`` — the
    listing tool returns a thin index without the image / price / rating
    fields a card needs). For each matching step we emit one attachment::

        {
          "kind": "menu_cards",
          "source": "<provider>.get_restaurant_menu_by_categories",
          "restaurant_name": "Subway",
          "res_id": 55634,
          "items": [
            {item_id, variant_id?, name, price?, discounted_price?,
             image_url?, rating?, votes?, description?, veg_kind?,
             category?},
            ...
          ]
        }

    The frontend renders this into a Zomato-style grid below the assistant
    bubble. The original ``step.result`` envelope is left untouched so
    the response LLM, observable trace, and debug payloads still see the
    raw structure.

    Idempotent against the Zomato executor's response-time menu trim
    (``_truncate_menu_envelope_for_response``) because we only iterate
    over whatever rows are present in the envelope — i.e. we always
    show *exactly* the items the LLM saw.
    """
    if not isinstance(raw_tool_results, dict):
        return []
    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            if tool not in _MENU_CARD_TOOLS:
                continue
            envelope = step.get("result")
            if not isinstance(envelope, dict) or envelope.get("is_error"):
                continue
            # The Zomato envelope nests the actual payload at
            # data.result.data — same structure the executor walks in
            # _listing_item_mappings_blob / _variant_price_map_from_category_menu.
            data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
            if not data:
                continue
            result = data.get("result") if isinstance(data.get("result"), dict) else None
            if not result:
                continue
            inner = result.get("data") if isinstance(result.get("data"), dict) else None
            if not inner:
                continue
            # Pick the rich row source (image / price / description live here):
            #   * by-categories tool → menu_items[]
            #   * listing tool       → partial_menu[]  (item_mappings[] is the
            #                                           thin "what the LLM saw"
            #                                           slice; partial_menu[] is
            #                                           the rich card-shaped one
            #                                           sitting alongside)
            rich_rows = inner.get("menu_items")
            if not isinstance(rich_rows, list) or not rich_rows:
                rich_rows = inner.get("partial_menu")
            if not isinstance(rich_rows, list) or not rich_rows:
                continue

            # Compute the visible-vid set + order so cards align exactly with
            # what the response LLM was shown and what ``session_menu`` will
            # carry next turn. Visible source is whichever slice the executor
            # trimmed:
            #   * listing tool  → item_mappings[] (already capped to 8)
            #   * by-categories → menu_items[]    (already capped to 8)
            visible_source = (
                inner.get("item_mappings")
                if isinstance(inner.get("item_mappings"), list)
                else rich_rows
            )
            visible_order: list[str] = []
            visible_set: set[str] = set()
            for v in visible_source:
                if not isinstance(v, dict):
                    continue
                vid = v.get("variant_id")
                if not isinstance(vid, str) or not vid.strip():
                    continue
                vid = vid.strip()
                if vid in visible_set:
                    continue
                visible_set.add(vid)
                visible_order.append(vid)

            # Index the rich rows by variant_id so we can both
            # (a) drop any row whose vid wasn't shown to the user, and
            # (b) emit cards in the same order as the visible list.
            rich_by_vid: dict[str, dict[str, Any]] = {}
            for it in rich_rows:
                if not isinstance(it, dict):
                    continue
                vid = it.get("variant_id")
                if not isinstance(vid, str) or not vid.strip():
                    continue
                rich_by_vid.setdefault(vid.strip(), it)

            cards: list[dict[str, Any]] = []
            for vid in visible_order:
                if len(cards) >= _MAX_MENU_CARDS:
                    break
                rich = rich_by_vid.get(vid)
                if rich is None:
                    continue
                card = _build_menu_card(rich)
                if card is not None:
                    cards.append(card)
            if not cards:
                continue
            attachment: dict[str, Any] = {
                "kind": "menu_cards",
                "source": f"{provider_name}.{tool}",
                "items": cards,
            }
            res_id = inner.get("res_id") or (step.get("arguments") or {}).get("res_id")
            if res_id not in (None, ""):
                attachment["res_id"] = res_id
            rname = inner.get("restaurant_name")
            if isinstance(rname, str) and rname.strip():
                attachment["restaurant_name"] = rname.strip()
            # Surface which categories were requested so the response LLM
            # can mention them naturally ("from the Recommended list...").
            args = step.get("arguments") or {}
            cats = args.get("categories") if isinstance(args, dict) else None
            if isinstance(cats, list):
                clean = [c.strip() for c in cats if isinstance(c, str) and c.strip()]
                if clean:
                    attachment["categories"] = clean
            out.append(attachment)
    return out


def _format_routing_message(
    current_message: str,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    session_state_keys: list[str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
    session_movies: list[dict[str, Any]] | None = None,
    session_theatres: list[dict[str, Any]] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
    selected_address_id: str | None = None,
    pending_variant_choice: list[dict[str, Any]] | None = None,
) -> str:
    """Build planner/orchestrator input: recent transcript + current user turn.

    When ``session_state_keys`` is non-empty, append a compact line listing the
    placeholder names the executor already remembers from prior turns (e.g.
    ``__CART_ID__``, ``__RESTAURANT_ID__``). Values are intentionally **not**
    included — the planner only needs to know *what* state exists so it can
    decide whether to re-run a tool or rely on placeholder substitution. The
    executor still owns the actual ID values.

    When ``session_restaurants`` is non-empty, append a small table of
    (name, res_id) pairs the user has already been shown. The planner can use
    these literal res_ids in tool arguments (e.g. ``get_menu_items_listing``
    or ``create_cart``) to skip a redundant — and often unreliable, since
    Zomato's keyword search is fuzzy — re-search.
    """
    current = (current_message or "").strip()
    parts: list[str] = []
    if conversation_history:
        recent = [
            item
            for item in conversation_history[-8:]
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and item.get("content")
        ]
        if recent:
            transcript = "\n".join(
                f"{item.get('role')}: {str(item.get('content') or '')[:700]}"
                for item in recent
            )
            parts.append(f"Conversation so far:\n{transcript}")
    if session_state_keys:
        keys = sorted({str(k) for k in session_state_keys if isinstance(k, str) and k.strip()})
        if keys:
            parts.append(
                "Carried-forward placeholders available (set by previous turns; "
                "use them directly in tool arguments — the executor will substitute "
                "the real values): " + ", ".join(keys)
            )
    if session_restaurants:
        # Cap how many we surface to the planner — too long bloats every prompt.
        # 10 is the same order of magnitude as a typical user-visible result list.
        rows: list[str] = []
        for entry in session_restaurants[:10]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            rid = str(entry.get("res_id") or "").strip()
            if not name or not rid:
                continue
            rating = entry.get("rating")
            tail = f" (rating {rating})" if rating not in (None, "") else ""
            rows.append(f"  - {name} → res_id={rid}{tail}")
        if rows:
            parts.append(
                "Restaurants already shown to the user this session — if the "
                "user picks one of these by name, use the literal res_id below "
                "instead of running get_restaurants_for_keyword again "
                "(Zomato's keyword search is fuzzy and often returns the wrong "
                "restaurant for short brand names):\n" + "\n".join(rows)
            )
    if session_addresses:
        # Surface the user's saved delivery addresses so the planner can
        # disambiguate "use my work address" without having to re-fetch.
        # Mark the currently-selected one so the planner doesn't propose
        # changing addresses when the user is just continuing a flow.
        sel = (selected_address_id or "").strip()
        addr_rows: list[str] = []
        for entry in session_addresses[:8]:
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
        if addr_rows:
            parts.append(
                "Saved delivery addresses available this session (executor "
                "auto-uses the first / starred one for `__ADDRESS_ID__`; "
                "switch ONLY when the user explicitly asks to change "
                "address, then put the chosen literal address_id in your "
                "tool args):\n" + "\n".join(addr_rows)
            )
    if session_movies:
        movie_rows: list[str] = []
        for entry in session_movies[:10]:
            if not isinstance(entry, dict):
                continue
            mid = str(entry.get("movie_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if mid and name:
                movie_rows.append(f"  - {name} → movie_id={mid}")
        if movie_rows:
            parts.append(
                "Movies already shown this session — use literal movie_id "
                "below for showtimes/reviews instead of re-searching:\n"
                + "\n".join(movie_rows)
            )
    if session_theatres:
        theatre_rows: list[str] = []
        for entry in session_theatres[:10]:
            if not isinstance(entry, dict):
                continue
            tid = str(entry.get("theatre_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if tid and name:
                theatre_rows.append(f"  - {name} → theatre_id={tid}")
        if theatre_rows:
            parts.append(
                "Theatres already shown this session — use literal theatre_id "
                "below for showtimes instead of re-searching:\n"
                + "\n".join(theatre_rows)
            )
    if session_menu:
        # Surface the recently-shown menu items so the planner can emit
        # ``create_cart`` directly when the user picks one (no redundant
        # menu fetch), and can use the addon_groups schema below to drive
        # customization picks without another round-trip.
        menu_rows: list[str] = []
        for entry in session_menu[:25]:
            if not isinstance(entry, dict):
                continue
            vid = str(entry.get("variant_id") or "").strip()
            iname = str(entry.get("item_name") or "").strip()
            if not vid or not iname:
                continue
            rid = str(entry.get("res_id") or "").strip()
            price_raw = entry.get("discounted_price") or entry.get("price")
            price = (
                f" — ₹{price_raw}"
                if price_raw not in (None, "", 0, "0")
                else ""
            )
            tail_bits: list[str] = []
            if rid:
                tail_bits.append(f"res_id={rid}")
            variants = entry.get("variants") or []
            if isinstance(variants, list) and variants:
                names = []
                for v in variants[:6]:
                    if not isinstance(v, dict):
                        continue
                    pv = v.get("property_values") or {}
                    if isinstance(pv, dict) and pv:
                        names.append(
                            "/".join(
                                f"{k}={vv}" for k, vv in pv.items() if vv
                            )
                            or str(v.get("variant_id") or "")
                        )
                if names:
                    tail_bits.append(f"variants: {', '.join(names)}")
            groups = entry.get("addon_groups") or []
            if isinstance(groups, list) and groups:
                gtags: list[str] = []
                for g in groups[:4]:
                    if not isinstance(g, dict):
                        continue
                    gname = str(g.get("name") or g.get("id") or "").strip()
                    mn = int(g.get("min") or 0)
                    mx = int(g.get("max") or 0)
                    if gname:
                        gtags.append(f"{gname} (min={mn}, max={mx})")
                if gtags:
                    tail_bits.append("addon_groups: " + "; ".join(gtags))
            tail = f" [{' | '.join(tail_bits)}]" if tail_bits else ""
            menu_rows.append(f"  - {iname} → variant_id={vid}{price}{tail}")
        if menu_rows:
            parts.append(
                "Menu items already shown to the user this session — when the "
                "user picks one by name, put the literal variant_id below into "
                "your `create_cart` items[] (no need to re-run "
                "get_menu_items_listing / get_restaurant_menu_by_categories). "
                "If the item has `variants:` listed, pick the variant_id whose "
                "property values best match what the user said (size/crust/"
                "etc.). If `addon_groups:` are listed with min>=1, the executor "
                "will auto-default missing required groups, but you can include "
                "explicit picks in items[].add_ons when the user names them:\n"
                + "\n".join(menu_rows)
            )
    if pending_variant_choice:
        # Surface the variant menu the previous turn deferred so the
        # planner can emit literal groupId / variation.id values when
        # the user replies with their pick ("medium new hand tossed").
        # Without this, the planner hallucinates ``__VARIANT_ID__`` /
        # ``__GROUP_ID__`` placeholders which the executor can't resolve.
        pvc_blocks: list[str] = []
        for item in pending_variant_choice[:3]:
            if not isinstance(item, dict):
                continue
            iname = str(item.get("item_name") or "").strip() or "(unnamed item)"
            iid = str(item.get("item_id") or "").strip()
            head = f"  - {iname} (item_id={iid}):" if iid else f"  - {iname}:"
            pvc_blocks.append(head)
            for group in (item.get("groups") or [])[:4]:
                if not isinstance(group, dict):
                    continue
                gname = str(group.get("name") or "Choose").strip()
                gid = str(group.get("groupId") or "").strip()
                pvc_blocks.append(
                    f"      group {gname!r} (group_id={gid}):"
                )
                for choice in (group.get("choices") or [])[:12]:
                    if not isinstance(choice, dict):
                        continue
                    cname = str(choice.get("name") or "").strip()
                    cid = str(choice.get("id") or "").strip()
                    price = choice.get("price")
                    price_tail = f" ₹{price}" if price not in (None, "") else ""
                    pvc_blocks.append(
                        f"        * {cname} → variation_id={cid}{price_tail}"
                    )
        if pvc_blocks:
            parts.append(
                "Pending variant choice from the previous turn — the user is "
                "replying with their pick now. Build a fresh `update_food_cart` "
                "with literal `cartItems[].variantsV2 = [{groupId: <group_id>, "
                "variations: [{id: <variation_id>}]}]` using the ids below. "
                "Pick one variation per group matching the user's words "
                "(\"medium\" → Size group's Medium variation, etc.). "
                "Never emit `__VARIANT_ID__` / `__GROUP_ID__` placeholders — "
                "they don't exist.\n" + "\n".join(pvc_blocks)
            )
    parts.append(f"Current user message:\n{current}")
    return "\n\n".join(parts) if len(parts) > 1 else current


_KNOWN_PROVIDER_IDS = frozenset(
    {"zomato", "swiggy_food", "swiggy_instamart", "zepto", "district", "kite", "groww", "daily_calorie_tracker"}
)


def _first_provider_error_code(raw_tool_results: dict[str, Any] | None) -> str | None:
    """Lift the first provider-level ``error_code`` to the top-level envelope.

    Currently only the District executor emits this (``needs_location``), but
    the lookup is provider-agnostic so any provider that wants to trigger the
    frontend's geolocation auto-prompt or other UI flow can opt in by adding
    ``error_code`` to its result dict.
    """
    if not isinstance(raw_tool_results, dict):
        return None
    for provider_name in sorted(raw_tool_results.keys()):
        raw = raw_tool_results.get(provider_name)
        if not isinstance(raw, dict):
            continue
        code = raw.get("error_code")
        if isinstance(code, str) and code.strip():
            return code.strip()
    return None


def _extract_previous_provider(
    conversation_history: list[dict[str, str]] | None,
) -> str | None:
    """Best-effort provider stickiness signal.

    Priority:
      1. Explicit ``provider`` field on the most recent assistant history item
         (forward-compatible with frontends that echo back routing info).
      2. Heuristic scan of the last few assistant turns for unambiguous
         provider markers in the message text. Prefers the *latest* non-empty
         signal to avoid drifting back to a stale provider.

    Returns ``None`` when nothing reliable is found — callers must treat that
    as "no hint" rather than as a default.
    """
    if not conversation_history:
        return None
    recent = [
        item
        for item in conversation_history[-6:]
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]
    for item in reversed(recent):
        pid = item.get("provider")
        if isinstance(pid, str) and pid.strip() in _KNOWN_PROVIDER_IDS:
            return pid.strip()
    for item in reversed(recent):
        content = str(item.get("content") or "").lower()
        if not content:
            continue
        has_instamart = "instamart" in content
        has_zepto = "zepto" in content
        has_zomato = "zomato" in content
        has_swiggy = "swiggy" in content and not has_instamart
        has_district = "district" in content
        if has_instamart:
            return "swiggy_instamart"
        if has_zepto:
            return "zepto"
        if has_district:
            return "district"
        if has_zomato:
            return "zomato"
        if has_swiggy:
            return "swiggy_food"
    return None


def _ensure_custom_providers_ready(routed_providers: set[str], user_id: str) -> None:
    for pid in sorted(routed_providers):
        tp = get_tool_provider(pid, user_id=user_id)
        if tp and tp.custom and not tp.tools:
            raise CustomMcpNeedsDiscoveryError(
                "Test this custom MCP first so Companion can discover its tools."
            )


def _empty_constraints_template() -> dict[str, Any]:
    return {
        "requires_address_resolution": False,
        "requested_location_text": None,
        "inferred_keyword": None,
        "price_max_inr": None,
        "price_min_inr": None,
        "min_rating": None,
        "near_and_fast": None,
        "vegetarian_only": None,
        "party_size_estimate": None,
        "health_constraints": None,
    }


def _non_food_plan(*, reasoning: str) -> dict[str, Any]:
    return {
        "intent": "unsupported_task",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints_template(),
        "execution_notes_for_downstream": "Task type is not wired to MCP tools yet.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _empty_food_plan(*, reasoning: str) -> dict[str, Any]:
    return {
        "intent": "clarify_request",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints_template(),
        "execution_notes_for_downstream": "No food MCP providers selected; respond conversationally.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _empty_provider_plan(*, reasoning: str, task_type: str) -> dict[str, Any]:
    return {
        "intent": "connect_or_discover_provider",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": {},
        "execution_notes_for_downstream": (
            f"{task_type} provider has no cached tool schema yet."
        ),
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _merge_provider_food_plans(
    labeled: list[tuple[str, dict[str, Any]]],
    *,
    resolved_model: str,
    routing_provider: str,
) -> dict[str, Any]:
    merged_steps: list[dict[str, Any]] = []
    summaries: list[str] = []
    intents: list[str] = []
    merged_constraints: dict[str, Any] = {}

    for pid, partial in labeled:
        st = str(partial.get("summary") or "").strip()
        if st:
            summaries.append(st)
        it = str(partial.get("intent") or "").strip()
        if it:
            intents.append(it)
        raw_c = partial.get("constraints")
        if isinstance(raw_c, dict):
            for key, val in raw_c.items():
                if key not in merged_constraints:
                    merged_constraints[key] = val
                elif key == "requires_address_resolution":
                    merged_constraints[key] = bool(merged_constraints.get(key)) or bool(val)
                elif merged_constraints.get(key) in (None, "", []) and val not in (None, "", []):
                    merged_constraints[key] = val

        tpl = partial.get("tool_plan")
        if not isinstance(tpl, list):
            continue
        for step in tpl:
            if not isinstance(step, dict):
                continue
            step_out = dict(step)
            if not str(step_out.get("provider") or "").strip():
                step_out["provider"] = pid
            merged_steps.append(step_out)

    if not merged_constraints:
        merged_constraints = _empty_constraints_template()

    summary_joined = " · ".join(summaries) if summaries else "Coordinated requests across providers."
    unique_intents = {i for i in intents if i}
    intent_pick = (
        next(iter(unique_intents)) if len(unique_intents) == 1 else "multi_provider_food_request"
    )

    return {
        "intent": intent_pick,
        "summary": summary_joined,
        "tool_plan": merged_steps,
        "constraints": merged_constraints,
        "execution_notes_for_downstream": (
            "Merged independent per-provider MCP plans; execute in parallel when possible."
        ),
        "validation": {
            "tool_count": len(merged_steps),
            "dropped_unknown_tool_steps": 0,
            "resolved_model": resolved_model,
            "completion_id": "",
            "provider": routing_provider,
        },
    }


def _plan_for_provider(
    provider_id: str,
    *,
    routing_message: str,
    llm: ResolvedLlm,
    user_id: str,
) -> dict[str, Any]:
    """Delegate to the provider's domain agent (LLM + prompt → tool_plan)."""
    tp = get_tool_provider(provider_id, user_id=user_id)
    trace("planner.in", provider=provider_id, routing_message=routing_message)

    if provider_id == "swiggy_food":
        from app.services.swiggy.planner import plan_swiggy_food

        out = plan_swiggy_food(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out
    if provider_id == "swiggy_instamart":
        from app.services.instamart.planner import plan_instamart

        out = plan_instamart(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out
    if provider_id == "zomato":
        from app.services.zomato.planner import plan_zomato_food

        out = plan_zomato_food(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out
    if provider_id == "district":
        from app.services.district.planner import plan_district

        out = plan_district(user_message=routing_message, llm=llm)
        trace("planner.out", provider=provider_id, plan=snip(out))
        return out

    if tp and not tp.tools:
        return _empty_provider_plan(
            reasoning=(
                f"{tp.label} needs account authorization and tool discovery before "
                "Companion can plan calls for it. Use MCP Connect/Test first."
            ),
            task_type="finance_investing",
        )

    return route_food_query(
        user_message=routing_message,
        api_key=llm.api_key,
        model=llm.model,
        provider=llm.provider,
        base_url=llm.base_url,
        tool_provider=provider_id,
        tool_provider_label=tp.label if tp else None,
        tool_provider_tools=(sorted(tp.tools) if tp and tp.tools else None),
    )


def _resolve_food_routing_plan(
    *,
    routing_message: str,
    routed_providers: set[str],
    llm: ResolvedLlm,
    user_id: str,
) -> dict[str, Any]:
    if not routed_providers:
        return _empty_food_plan(
            reasoning="No food MCP providers remain after orchestration.",
        )

    if len(routed_providers) == 1:
        pid = next(iter(routed_providers))
        return _plan_for_provider(
            pid,
            routing_message=routing_message,
            llm=llm,
            user_id=user_id,
        )

    labeled: list[tuple[str, dict[str, Any]]] = []
    for pid in sorted(routed_providers):
        labeled.append(
            (
                pid,
                _plan_for_provider(
                    pid,
                    routing_message=routing_message,
                    llm=llm,
                    user_id=user_id,
                ),
            )
        )

    return _merge_provider_food_plans(
        labeled,
        resolved_model=llm.model,
        routing_provider=llm.provider,
    )


def run_agent_turn(
    *,
    user_message: str,
    address_id: str,
    execute_tools: bool,
    user_id: str,
    llm: ResolvedLlm,
    conversation_history: list[dict[str, str]] | None = None,
    user_geo: dict[str, Any] | None = None,
    session_state: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_addresses: list[dict[str, Any]] | None = None,
    session_movies: list[dict[str, Any]] | None = None,
    session_theatres: list[dict[str, Any]] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
    pending_variant_choice: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Orchestrated agent turn producing the same response envelope as legacy /api/food/query/.

    Flow: orchestrator LLM → per-provider domain LLM (tool_plan) → MCP executor → response LLM.

    ``session_state`` carries forward IDs the executor learned on previous turns
    (cart_id, restaurant_id, order_id, …). Frontends should echo back the
    ``session_state`` returned from the previous turn's response. This is the
    mechanism that allows a Zomato cart created in turn N to be checked out
    in turn N+1 without the LLM ever having to see raw IDs.

    ``session_restaurants`` is the LRU-style list of every (name, res_id) pair
    the user has been shown across the session. It lets the planner pick a
    restaurant the user has seen *without* re-running Zomato's unreliable
    fuzzy keyword search. Like ``session_state``, frontends should echo back
    whatever the previous turn returned.
    """
    new_turn()
    # Normalize the restaurants seed once so we can both echo it back and
    # surface it in the planner prompt below.
    seed_restaurants: list[dict[str, Any]] = []
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
            seed_restaurants.append({**entry, "res_id": rid.strip(), "name": name.strip()})

    seed_movies: list[dict[str, Any]] = []
    if isinstance(session_movies, list):
        for entry in session_movies:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("movie_id")
            name = entry.get("name")
            if isinstance(mid, str) and mid.strip() and isinstance(name, str) and name.strip():
                seed_movies.append({**entry, "movie_id": mid.strip(), "name": name.strip()})

    seed_theatres: list[dict[str, Any]] = []
    if isinstance(session_theatres, list):
        for entry in session_theatres:
            if not isinstance(entry, dict):
                continue
            tid = entry.get("theatre_id")
            name = entry.get("name")
            if isinstance(tid, str) and tid.strip() and isinstance(name, str) and name.strip():
                seed_theatres.append({**entry, "theatre_id": tid.strip(), "name": name.strip()})

    # ``session_menu`` carry-forward — same shape as the Zomato executor
    # emits and consumes. Each entry must have a real ``variant_id`` +
    # ``item_name``; richer metadata (variants[], addon_groups[]) flows
    # through verbatim.
    seed_menu: list[dict[str, Any]] = []
    if isinstance(session_menu, list):
        for entry in session_menu:
            if not isinstance(entry, dict):
                continue
            vid = entry.get("variant_id")
            name = entry.get("item_name") or entry.get("name")
            if not isinstance(vid, str) or not vid.strip():
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            seed_menu.append({**entry, "variant_id": vid.strip(), "item_name": name.strip()})

    # Saved-address seed (parallel to seed_restaurants). Echoed back to
    # the client so each session keeps a stable address index, and surfaced
    # to the planner so it can disambiguate "use my work address" without
    # a fresh MCP round-trip.
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

    trace(
        "pipeline.in",
        user_id=user_id,
        user_message=user_message,
        address_id=address_id,
        history_turns=len(conversation_history or []),
        execute_tools=execute_tools,
        session_state_keys=sorted((session_state or {}).keys()),
        seed_addresses=len(seed_addresses),
    )
    # Orchestrator sees ONLY the transcript — it classifies task_type and
    # provider, neither of which should depend on stored placeholders.
    routing_message = _format_routing_message(user_message, conversation_history)
    # Planner needs the placeholder hint AND the recent-restaurants index so
    # it can (a) skip a redundant restaurant search when the user picks a
    # known restaurant and (b) avoid re-running upstream tools when downstream
    # state is already known.
    # ``selected_address_id`` lets the planner star the address currently
    # being used in the prompt — same shape as the "*" marker for the
    # active restaurant in the index. Prefer the explicit request param,
    # then fall back to whatever the executor learned on a prior turn.
    selected_addr = (address_id or "").strip() or (
        str((session_state or {}).get("__ADDRESS_ID__") or "").strip() or None
    )
    # Sanitize the pending_variant_choice echo so a malformed client
    # can't inject arbitrary strings into the planner prompt.
    seed_variant_choices: list[dict[str, Any]] = []
    if isinstance(pending_variant_choice, list):
        for entry in pending_variant_choice:
            if not isinstance(entry, dict):
                continue
            if not str(entry.get("item_id") or "").strip():
                continue
            seed_variant_choices.append(entry)

    planner_routing_message = _format_routing_message(
        user_message,
        conversation_history,
        session_state_keys=sorted((session_state or {}).keys()) or None,
        session_restaurants=seed_restaurants or None,
        session_addresses=seed_addresses or None,
        session_movies=seed_movies or None,
        session_theatres=seed_theatres or None,
        session_menu=seed_menu or None,
        selected_address_id=selected_addr,
        pending_variant_choice=seed_variant_choices or None,
    )
    previous_provider = _extract_previous_provider(conversation_history)
    trace(
        "orchestrator.in",
        routing_message=routing_message,
        previous_provider=previous_provider,
    )
    orchestration = classify_agent_routing(
        user_message=routing_message,
        llm=llm,
        previous_provider=previous_provider,
    )
    trace("orchestrator.out", orchestration=snip(orchestration))

    task_type = str(orchestration.get("task_type") or "food_delivery")
    if task_type == "finance_investing":
        routed = set(orchestration.get("finance_providers") or [])
    elif task_type == "health_nutrition":
        routed = set(orchestration.get("health_providers") or [])
    else:
        routed = set(orchestration.get("food_providers") or [])
    # Going-out is District-only; if the orchestrator forgot to include it
    # (legacy LLM responses pre-prompt-refresh), force district in.
    if task_type == "going_out" and not routed:
        routed = {"district"}
    routed = {
        pid
        for pid in routed
        if isinstance(pid, str) and pid.strip() and pid.strip() != "blinkit"
    }

    if task_type == "whatsapp_message":
        from app.services.whatsapp.planner import plan_whatsapp_message
        from app.services.whatsapp.client import WhatsAppClient
        
        plan = plan_whatsapp_message(user_message=routing_message, user_id=user_id, llm=llm)
        whatsapp_result = None
        if execute_tools and plan.get("intent") == "send_whatsapp":
            client = WhatsAppClient()
            whatsapp_result = client.send_text_message(
                to=plan["recipient_phone"],
                text=plan["message"]
            )
        
        # Build a response message
        if plan.get("intent") == "send_whatsapp":
            if whatsapp_result and "error" not in whatsapp_result:
                assistant_message = f"Sent WhatsApp to {plan['recipient_name']}: \"{plan['message']}\""
            elif execute_tools:
                assistant_message = f"Failed to send WhatsApp to {plan['recipient_name']}. Please check credentials."
            else:
                assistant_message = f"I'll send a WhatsApp to {plan['recipient_name']} with the message: \"{plan['message']}\". (Execution skipped)"
        else:
            assistant_message = plan.get("reasoning") or "I couldn't figure out who to send that WhatsApp message to."

        return {
            "status": "success",
            "provider": llm.provider,
            "intent_routing": orchestration,
            "whatsapp_plan": plan,
            "whatsapp_result": whatsapp_result,
            "assistant_message": assistant_message,
        }

    if task_type == "normal_query":
        routed = set()
        plan = plan_normal_query(reasoning=str(orchestration.get("reasoning") or ""))
    elif task_type == "reminder_calendar":
        routed = set()
        plan = plan_reminder_calendar(
            reasoning=str(orchestration.get("reasoning") or ""),
            user_message=user_message,
        )
    elif task_type == "maps_query":
        routed = set()
        plan = plan_maps_query(
            reasoning=str(orchestration.get("reasoning") or ""),
            user_message=user_message,
            llm=llm,
        )
    elif task_type in {"finance_investing", "health_nutrition"}:
        if not routed:
            plan = _empty_provider_plan(
                reasoning=str(orchestration.get("reasoning") or "No MCP provider selected."),
                task_type=task_type,
            )
        else:
            _ensure_custom_providers_ready(routed, user_id)
            plan = _resolve_food_routing_plan(
                routing_message=planner_routing_message,
                routed_providers=routed,
                llm=llm,
                user_id=user_id,
            )
    elif task_type not in ("food_delivery", "going_out"):
        plan = _non_food_plan(reasoning=str(orchestration.get("reasoning") or task_type))
    elif not routed:
        plan = _empty_food_plan(reasoning=str(orchestration.get("reasoning") or ""))
    else:
        _ensure_custom_providers_ready(routed, user_id)
        plan = _resolve_food_routing_plan(
            routing_message=planner_routing_message,
            routed_providers=routed,
            llm=llm,
            user_id=user_id,
        )

    tool_execution: dict[str, Any] | None = None
    calendar_execution: dict[str, Any] | None = None
    maps_execution: dict[str, Any] | None = None
    provider_summaries: dict[str, Any] = {}
    zomato_summary = build_zomato_summary(None)
    raw_tool_results: dict[str, Any] = {}

    if task_type == "reminder_calendar" and execute_tools:
        calendar_execution = execute_reminder_calendar(
            user_message=user_message,
            user_id=user_id,
        )
    elif task_type == "maps_query" and execute_tools:
        maps_execution = execute_maps_query(
            plan=plan,
            user_geo=user_geo,
        )
    elif execute_tools:
        tool_plan = plan.get("tool_plan") if isinstance(plan, dict) else []
        enabled_providers = routed or None
        trace(
            "executor.in",
            providers=sorted(enabled_providers or []),
            tool_plan=snip(tool_plan),
            address_id=address_id,
            constraints=snip(plan.get("constraints") if isinstance(plan, dict) else {}),
        )
        tool_execution = execute_tool_plan_sync(
            tool_plan if isinstance(tool_plan, list) else [],
            address_id=address_id,
            constraints=plan.get("constraints") if isinstance(plan, dict) else {},
            user_message=user_message,
            enabled_providers=enabled_providers,
            user_id=user_id,
            session_placeholders=session_state,
            session_restaurants=seed_restaurants or None,
            session_addresses=seed_addresses or None,
            session_movies=seed_movies or None,
            session_theatres=seed_theatres or None,
            session_menu=seed_menu or None,
            user_geo=user_geo,
        )
        provider_results = tool_execution.get("provider_results")
        if isinstance(provider_results, dict):
            for provider_name, raw in provider_results.items():
                if isinstance(raw, dict):
                    raw_tool_results[provider_name] = raw
                    provider_summaries[provider_name] = build_zomato_summary(raw)
            zraw = provider_results.get("zomato")
            zomato_summary = build_zomato_summary(zraw if isinstance(zraw, dict) else None)
        trace(
            "executor.out",
            ok=tool_execution.get("ok"),
            error=tool_execution.get("error"),
            steps_by_provider={
                p: len((r or {}).get("steps") or [])
                for p, r in (provider_results or {}).items()
            },
            diagnostics_by_provider={
                p: snip((r or {}).get("diagnostics") or {})
                for p, r in (provider_results or {}).items()
            },
        )

    domain_execution: dict[str, Any] | None = (
        calendar_execution
        if task_type == "reminder_calendar"
        else maps_execution
        if task_type == "maps_query"
        else None
    )

    # Hoist any inline image attachments (currently only the Zomato checkout
    # payment QR) so the chat UI can render them next to the assistant bubble.
    # The base64 lives buried under `raw_tool_results.<provider>.steps[].result.payment`;
    # the LLM-written `assistant_message` cannot embed an image (the frontend's
    # markdown renderer doesn't accept image syntax, and the response-LLM is
    # told never to paste raw data), so we surface attachments as a separate
    # top-level field with a stable schema.
    attachments = _collect_payment_attachments(raw_tool_results)
    menu_attachments = _collect_menu_attachments(raw_tool_results)
    if menu_attachments:
        attachments = attachments + menu_attachments
    trace(
        "hoister.out",
        attachment_count=len(attachments),
        payment_attachments=len(attachments) - len(menu_attachments),
        menu_attachments=len(menu_attachments),
        attachments=snip(attachments),
    )

    # Build the outgoing session_state. Start from whatever the caller sent in
    # (so IDs that weren't touched this turn carry forward — e.g. a restaurant
    # id from turn 1 still applies in turn 4) and overlay the values the
    # executor learned this turn. Executor wins for any collision because it
    # just observed the live MCP state.
    next_session_state: dict[str, str] = {}
    if isinstance(session_state, dict):
        for k, v in session_state.items():
            if isinstance(k, str) and isinstance(v, (str, int)) and str(v).strip():
                next_session_state[k] = str(v).strip()
    # Start the next-turn restaurant index from the seed; the executor's
    # diagnostics will override it if the current turn ran a search.
    next_session_restaurants: list[dict[str, Any]] = list(seed_restaurants)
    next_session_movies: list[dict[str, Any]] = list(seed_movies)
    next_session_theatres: list[dict[str, Any]] = list(seed_theatres)
    # Same pattern for the per-item LRU — seed-first, then overwrite with
    # whatever the executor emitted (richness-aware merge already done
    # inside the executor).
    next_session_menu: list[dict[str, Any]] = list(seed_menu)
    # Same pattern for saved addresses — if the executor (re)fetched the
    # list this turn, its result wins; otherwise we just echo the seed
    # back so the frontend can keep persisting it.
    next_session_addresses: list[dict[str, Any]] = list(seed_addresses)
    # ``pending_variant_choice`` is single-turn state: if the executor
    # deferred a cart this turn we echo it back so the next turn's
    # planner can resolve the user's pick. If the executor did NOT
    # defer (turn succeeded or never tried), we DO NOT echo it back —
    # the choice was either satisfied or no longer relevant.
    next_pending_variant_choice: list[dict[str, Any]] = []
    address_source: str | None = None
    address_context: dict[str, Any] | None = None
    for _provider_name, _raw in (raw_tool_results or {}).items():
        if not isinstance(_raw, dict):
            continue
        _diag = _raw.get("diagnostics")
        if not isinstance(_diag, dict):
            continue
        _learned = _diag.get("session_placeholders")
        if isinstance(_learned, dict):
            for k, v in _learned.items():
                if isinstance(k, str) and isinstance(v, (str, int)) and str(v).strip():
                    next_session_state[k] = str(v).strip()
        _pvc = _diag.get("pending_variant_choice")
        if isinstance(_pvc, list) and _pvc:
            for entry in _pvc:
                if isinstance(entry, dict) and entry.get("item_id"):
                    next_pending_variant_choice.append(entry)
        _restos = _diag.get("session_restaurants")
        if isinstance(_restos, list) and _restos:
            # Executor already merged seed + new entries, so its list is the
            # authoritative next state. Overwriting (not appending) is what
            # keeps the LRU ordering meaningful — most-recent at the front.
            cleaned: list[dict[str, Any]] = []
            for entry in _restos:
                if not isinstance(entry, dict):
                    continue
                rid = entry.get("res_id")
                name = entry.get("name")
                if isinstance(rid, str) and rid.strip() and isinstance(name, str) and name.strip():
                    cleaned.append({**entry, "res_id": rid.strip(), "name": name.strip()})
            if cleaned:
                next_session_restaurants = cleaned
        _movies = _diag.get("session_movies")
        if isinstance(_movies, list) and _movies:
            cleaned_m: list[dict[str, Any]] = []
            for entry in _movies:
                if not isinstance(entry, dict):
                    continue
                mid = entry.get("movie_id")
                name = entry.get("name")
                if isinstance(mid, str) and mid.strip() and isinstance(name, str) and name.strip():
                    cleaned_m.append({**entry, "movie_id": mid.strip(), "name": name.strip()})
            if cleaned_m:
                next_session_movies = cleaned_m
        _theatres = _diag.get("session_theatres")
        if isinstance(_theatres, list) and _theatres:
            cleaned_t: list[dict[str, Any]] = []
            for entry in _theatres:
                if not isinstance(entry, dict):
                    continue
                tid = entry.get("theatre_id")
                name = entry.get("name")
                if isinstance(tid, str) and tid.strip() and isinstance(name, str) and name.strip():
                    cleaned_t.append({**entry, "theatre_id": tid.strip(), "name": name.strip()})
            if cleaned_t:
                next_session_theatres = cleaned_t
        _menu = _diag.get("session_menu")
        if isinstance(_menu, list) and _menu:
            cleaned_menu: list[dict[str, Any]] = []
            for entry in _menu:
                if not isinstance(entry, dict):
                    continue
                vid = entry.get("variant_id")
                iname = entry.get("item_name") or entry.get("name")
                if not isinstance(vid, str) or not vid.strip():
                    continue
                if not isinstance(iname, str) or not iname.strip():
                    continue
                cleaned_menu.append(
                    {**entry, "variant_id": vid.strip(), "item_name": iname.strip()}
                )
            if cleaned_menu:
                next_session_menu = cleaned_menu
        _addrs = _diag.get("session_addresses")
        if isinstance(_addrs, list) and _addrs:
            cleaned_a: list[dict[str, Any]] = []
            for entry in _addrs:
                if not isinstance(entry, dict):
                    continue
                aid = entry.get("address_id") or entry.get("addressId") or entry.get("id")
                if aid is None:
                    continue
                sid = str(aid).strip()
                if not sid:
                    continue
                cleaned_a.append({**entry, "address_id": sid})
            if cleaned_a:
                next_session_addresses = cleaned_a
        _src = _diag.get("address_source")
        if isinstance(_src, str) and _src.strip():
            address_source = _src.strip()

    # Build a compact `address_context` for the response LLM. Lets the
    # reply nudge the user ("using your home address — change kar dena ho
    # toh batao") without leaking raw IDs into the reply.
    if next_session_addresses or next_session_state.get("__ADDRESS_ID__"):
        active_aid = (next_session_state.get("__ADDRESS_ID__") or "").strip()
        active_label: str | None = None
        for entry in next_session_addresses:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("address_id") or "").strip() == active_aid:
                active_label = str(
                    entry.get("label")
                    or entry.get("location_name")
                    or entry.get("addressTag")
                    or entry.get("address_tag")
                    or entry.get("address_line")
                    or ""
                ).strip() or None
                break
        # Treat "auto" as the source whenever the address wasn't seeded
        # from the request and wasn't already in the inbound session_state.
        if not address_source:
            if (address_id or "").strip():
                address_source = "request"
            elif (session_state or {}).get("__ADDRESS_ID__"):
                address_source = "session"
            elif active_aid:
                address_source = "auto"
        address_context = {
            "address_id": active_aid or None,
            "label": active_label,
            "source": address_source,
            "options_count": len(next_session_addresses),
        }
    trace(
        "session_state.out",
        in_keys=sorted((session_state or {}).keys()),
        out_keys=sorted(next_session_state.keys()),
        # Mask values: the keys are enough to debug stickiness, values can
        # contain customer-identifying IDs we don't want in trace logs.
        delta_keys=sorted(
            set(next_session_state.keys()) - set((session_state or {}).keys())
        ),
    )

    out: dict[str, Any] = {
        "status": "success",
        "provider": llm.provider,
        # Single "primary" routed provider so frontends can echo it back on the
        # next turn's assistant history item as `provider`, giving the
        # orchestrator a deterministic stickiness signal (see RCA: silent
        # swiggy_food fallback). Falls back to the first routed provider in
        # alphabetical order when multiple providers ran (rare: compare flows).
        "routed_provider": (next(iter(sorted(routed))) if routed else None),
        "intent_routing": orchestration,
        "selected_tool_providers": sorted(routed),
        "food_routing_plan": plan,
        "tool_execution": {
            "requested": bool(execute_tools),
            "routed_providers": (
                domain_execution.get("routed_providers", [])
                if isinstance(domain_execution, dict)
                else (
                    tool_execution.get("routed_providers", [])
                    if isinstance(tool_execution, dict)
                    else []
                )
            ),
            "ok": (
                domain_execution.get("ok")
                if isinstance(domain_execution, dict)
                else (tool_execution.get("ok") if isinstance(tool_execution, dict) else False)
            ),
            "error": (
                domain_execution.get("error")
                if isinstance(domain_execution, dict)
                else (tool_execution.get("error") if isinstance(tool_execution, dict) else None)
            ),
            "error_code": (
                domain_execution.get("error_code")
                if isinstance(domain_execution, dict)
                else _first_provider_error_code(raw_tool_results)
            ),
        },
        "calendar_execution": calendar_execution,
        "maps_execution": maps_execution,
        "observable_trace": build_tool_execution_trace(
            plan=plan if isinstance(plan, dict) else {},
            selected_providers=routed,
            tool_execution=(
                tool_execution
                if execute_tools and task_type not in {"reminder_calendar", "maps_query"}
                else None
            ),
            intent_routing=orchestration,
            calendar_execution=calendar_execution if execute_tools else None,
        ),
        "zomato_summary": zomato_summary,
        "provider_summaries": provider_summaries,
        "attachments": attachments,
        # Frontends should treat this as opaque and echo it back on the next
        # turn's request. Keys are the placeholder names the planner emits
        # (__CART_ID__, __RESTAURANT_ID__, __ORDER_ID__, ...); values are the
        # IDs the executor learned from MCP responses.
        "session_state": next_session_state,
        # Multi-restaurant memory — see ``run_agent_turn`` docstring. Echo
        # back as-is on the next turn's request.
        "session_restaurants": next_session_restaurants,
        "session_movies": next_session_movies,
        "session_theatres": next_session_theatres,
        # Per-item LRU memory — every (res_id, variant_id, addon_groups)
        # tuple the user has been shown this session. Lets the next turn's
        # planner emit ``create_cart`` directly when the user picks one,
        # and carries the customization schema across turns so addon picks
        # ("with buttermilk") can be resolved against the already-known
        # leaf options.
        "session_menu": next_session_menu,
        # Saved-address memory — same echo contract as session_restaurants.
        # Lets the chat work with zero "fetch addresses" clicks on the
        # frontend: turn 1 auto-prefetches Zomato addresses, turns 2+ just
        # round-trip the list back and skip the MCP call.
        "session_addresses": next_session_addresses,
        # Compact view the chat UI / debug surface can show without having to
        # cross-reference session_state + session_addresses themselves.
        "address_context": address_context,
        # Single-turn deferral: if the executor skipped ``update_food_cart``
        # because the menu item has a real user choice (size 8"/10"/12",
        # crust hand-tossed vs thin), the variant menu rides here so the
        # next turn's planner can emit literal variation ids when the user
        # replies. Empty list when nothing is pending — frontends should
        # echo it back verbatim.
        "pending_variant_choice": next_pending_variant_choice,
    }
    trace(
        "response_builder.in",
        task_type=task_type,
        attachment_count=len(attachments),
        raw_tool_results_summary={
            p: {
                "ok": (r or {}).get("ok"),
                "steps": len((r or {}).get("steps") or []),
                "skipped_tools": ((r or {}).get("diagnostics") or {}).get("skipped_tools"),
            }
            for p, r in (raw_tool_results or {}).items()
        },
    )
    if task_type == "normal_query":
        assistant_message = general_chat_responder.reply_general_chat(
            user_message=user_message,
            llm=llm,
            conversation_history=conversation_history,
        )
    elif task_type == "reminder_calendar":
        assistant_message = reply_reminder_calendar(
            user_message=user_message,
            execution=calendar_execution,
            execute_tools=execute_tools,
        )
    elif task_type == "maps_query":
        assistant_message = reply_maps_query(
            user_message=user_message,
            execution=maps_execution,
            execute_tools=execute_tools,
        )
    else:
        response_payload = build_assistant_message_with_llm(
            user_message=user_message,
            plan=plan,
            tool_results=raw_tool_results,
            provider=llm.provider,
            api_key=llm.api_key,
            model=llm.model,
            base_url=llm.base_url,
            conversation_history=conversation_history,
            attachments=attachments,
            address_context=address_context,
        )
        assistant_message = str(response_payload.get("text") or "").strip()
        featured_ids = response_payload.get("featured_variant_ids")
        # LLM-driven menu_cards filter: the response LLM tells us exactly
        # which items it mentioned/recommended. Show cards ONLY for those
        # variant_ids — never the full cached top-8. This keeps the visual
        # grid in lockstep with what the user just heard ("here are 3 veg
        # picks" → exactly 3 cards, not 8).
        #
        # featured_ids semantics:
        #   * list[str] (possibly empty) → LLM complied with the contract.
        #     Filter cards to the listed ids (empty list ⇒ hide all cards).
        #   * None → LLM fell back to plain text (JSON parse failed).
        #     Leave attachments untouched so the chat doesn't lose its
        #     visual grid because the model hedged on format.
        if isinstance(featured_ids, list) and attachments:
            allowed = {v for v in featured_ids if isinstance(v, str) and v.strip()}
            filtered: list[dict[str, Any]] = []
            for att in attachments:
                if not isinstance(att, dict) or str(att.get("kind") or "") != "menu_cards":
                    filtered.append(att)
                    continue
                items = att.get("items") or []
                if not isinstance(items, list):
                    continue
                kept = [
                    it for it in items
                    if isinstance(it, dict)
                    and isinstance(it.get("variant_id"), str)
                    and it["variant_id"].strip() in allowed
                ]
                if not kept:
                    # No cards survived the filter → drop the whole
                    # attachment so the UI doesn't render an empty "Menu"
                    # header. The LLM's text stands on its own.
                    continue
                filtered.append({**att, "items": kept})
            attachments = filtered
            out["attachments"] = attachments
            trace(
                "response_builder.cards_filtered",
                featured_count=len(allowed),
                attachment_count=len(attachments),
            )
    trace("response_builder.out", assistant_message=assistant_message)
    out["assistant_message"] = assistant_message
    trace(
        "pipeline.out",
        routed_provider=out.get("routed_provider"),
        attachment_count=len(out.get("attachments") or []),
        tool_execution_ok=(out.get("tool_execution") or {}).get("ok"),
        tool_execution_error=(out.get("tool_execution") or {}).get("error"),
    )
    return out
