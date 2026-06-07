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
from app.services.uber import (
    execute_uber_cab,
    plan_uber_cab,
    reply_uber_cab,
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

# Tools whose envelopes carry restaurant-search results we can render as
# restaurant cards (image + rating + ETA + a few featured dishes inline).
#   * get_restaurants_for_keyword → results[] with embedded items[]
# Same lift-from-raw-envelope pattern as menu_cards; the chat-side
# <RestaurantCardGrid /> component renders these next to the assistant
# bubble. The featured items inside each card are real addable variants —
# the executor also stamps them into ``session_menu`` (see
# ``_collect_menu_index_from_keyword_envelope``) so the next-turn planner
# can ``create_cart`` directly from any visible item without a separate
# ``get_menu_items_listing`` round trip.
_RESTAURANT_CARD_TOOLS = {
    "get_restaurants_for_keyword",
}

# Max featured items we render per restaurant card. Matches the executor's
# ``_KEYWORD_ITEMS_PER_RESTAURANT`` (3) so the cards on screen, the
# response LLM's narration, and ``session_menu`` always agree.
_MAX_FEATURED_ITEMS_PER_RESTAURANT = 3

# Max restaurant cards shipped in one attachment. Matches the executor's
# ``_MAX_REMEMBERED_RESTAURANTS`` so the visible card row mirrors the
# planner's ``session_restaurants`` LRU exactly.
_MAX_RESTAURANT_CARDS = 8


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


def _build_restaurant_card_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one Zomato keyword-search ``results[].items[]`` row into a card item.

    Source shape (from ``outputs/get_restaurants_for_keyword_*.json``)::

        {variant_id, catalogue_id, name, price, min_price, image_link,
         is_veg, item_customizations, dish_selected_attr}

    Returns ``None`` for rows missing ``name`` + ``variant_id`` (those are
    the minimum bits the frontend needs to render a tile + the next-turn
    planner needs to add it to a cart).
    """
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    vid = item.get("variant_id")
    if not isinstance(vid, str) or not vid.strip():
        return None
    out: dict[str, Any] = {"name": name.strip(), "variant_id": vid.strip()}
    item_id = item.get("catalogue_id") or item.get("item_id")
    if isinstance(item_id, str) and item_id.strip():
        out["item_id"] = item_id.strip()
    price = item.get("price")
    if isinstance(price, (int, float)) and price > 0:
        out["price"] = price
    min_price = item.get("min_price")
    if isinstance(min_price, (int, float)) and min_price > 0 and min_price != price:
        out["min_price"] = min_price
    image = item.get("image_link") or item.get("image_url")
    if isinstance(image, str) and image.strip().startswith(("http://", "https://")):
        out["image_url"] = image.strip()
    is_veg = item.get("is_veg")
    if isinstance(is_veg, bool):
        out["veg_kind"] = "veg" if is_veg else "non_veg"
    return out


def _build_restaurant_card_entry(resto: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one ``results[]`` restaurant row into a renderable card entry.

    Carries enough metadata for the chat-side <RestaurantCardGrid /> to
    render the inspired Zomato-style tile (image header, rating chip,
    distance + ETA meta row, offer ribbon) PLUS up to
    ``_MAX_FEATURED_ITEMS_PER_RESTAURANT`` featured dishes the user can
    add directly.

    Returns ``None`` if the row is missing the basics (res_id + name) —
    those would render as a blank tile and confuse the user.
    """
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
    for k_src, k_dst in (
        ("rating", "rating"),
        ("votes", "votes"),
        ("eta", "eta"),
        ("distance", "distance_km"),
        ("res_image", "res_image"),
        ("res_offer", "res_offer"),
        ("serviceability_status", "serviceability_status"),
        ("chain_id", "chain_id"),
    ):
        v = resto.get(k_src)
        if v is None:
            continue
        if isinstance(v, str):
            vs = v.strip()
            if vs:
                out[k_dst] = vs
            continue
        out[k_dst] = v
    raw_items = resto.get("items")
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for it in raw_items:
            card = _build_restaurant_card_item(it)
            if card is None:
                continue
            items.append(card)
            if len(items) >= _MAX_FEATURED_ITEMS_PER_RESTAURANT:
                break
    if items:
        out["items"] = items
    return out


def _collect_restaurant_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Lift Zomato keyword-search ``results[]`` into renderable restaurant cards.

    Walks ``raw_tool_results[<provider>].steps[]`` looking for
    ``get_restaurants_for_keyword`` envelopes. For each matching step we
    emit one attachment::

        {
          "kind": "restaurant_cards",
          "source": "zomato.get_restaurants_for_keyword",
          "keyword": "burger",
          "restaurants": [
            {res_id, name, rating?, votes?, eta?, distance_km?, res_image?,
             res_offer?, serviceability_status?,
             items: [{variant_id, name, price?, image_url?, veg_kind?}, ...]},
            ...
          ]
        }

    Capped to ``_MAX_RESTAURANT_CARDS`` outer entries (matches the
    executor's session_restaurants LRU cap) and
    ``_MAX_FEATURED_ITEMS_PER_RESTAURANT`` inner items per card.

    The original ``step.result`` envelope is left untouched so the
    response-LLM, observable trace, and debug payloads still see the raw
    structure.
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
            if tool not in _RESTAURANT_CARD_TOOLS:
                continue
            envelope = step.get("result")
            if not isinstance(envelope, dict) or envelope.get("is_error"):
                continue
            data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
            results: list[Any] = []
            if isinstance(data, dict):
                if isinstance(data.get("results"), list):
                    results = data["results"]
                elif isinstance(data.get("data"), dict) and isinstance(
                    data["data"].get("results"), list
                ):
                    results = data["data"]["results"]
            if not results:
                continue
            cards: list[dict[str, Any]] = []
            seen: set[str] = set()
            for resto in results:
                if len(cards) >= _MAX_RESTAURANT_CARDS:
                    break
                entry = _build_restaurant_card_entry(resto)
                if entry is None:
                    continue
                if entry["res_id"] in seen:
                    continue
                seen.add(entry["res_id"])
                cards.append(entry)
            if not cards:
                continue
            attachment: dict[str, Any] = {
                "kind": "restaurant_cards",
                "source": f"{provider_name}.{tool}",
                "provider": "zomato",
                "restaurants": cards,
            }
            args = step.get("arguments") or {}
            if isinstance(args, dict):
                keyword = args.get("keyword") or args.get("query")
                if isinstance(keyword, str) and keyword.strip():
                    attachment["keyword"] = keyword.strip()
            out.append(attachment)
    return out


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
                "provider": "zomato",
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


def _build_cart_card_attachment(
    snapshot: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any] | None:
    """Wrap a normalized cart snapshot into a ``cart_card`` attachment.

    Snapshot is the output of ``zomato/mcp_executor._extract_cart_snapshot``
    — already trimmed to the fields the frontend's <ZomatoCart /> reads
    (cart_id, items[…], restaurant_*, charge_breakdown, totals, eta, etc.).

    Returns ``None`` if the snapshot doesn't carry the bare minimum to
    render (cart_id + at least one item) — defensive guard against echoing
    a stale/empty seed that would render an empty card.
    """
    if not isinstance(snapshot, dict):
        return None
    cid = snapshot.get("cart_id")
    items = snapshot.get("items")
    if not (isinstance(cid, str) and cid.strip() and isinstance(items, list) and items):
        return None
    # ``provider`` is decoded from the ``source`` string (e.g.
    # ``"zomato_food.create_cart"`` → "zomato", ``"swiggy_food.update_food_cart"``
    # → "swiggy"). The frontend keys cart-card styling + the "Order now"
    # checkout prompt off this field.
    provider = "zomato"
    src_lower = source.lower()
    if src_lower.startswith("swiggy"):
        provider = "swiggy"
    return {
        "kind": "cart_card",
        "source": source,
        "provider": provider,
        "cart": snapshot,
    }


def _collect_cart_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Auto-emit a ``cart_card`` attachment whenever ``create_cart`` ran this turn.

    Mirrors the ``restaurant_cards`` / ``menu_cards`` pattern: the executor
    flags ``diagnostics.fresh_cart_captured`` immediately after a successful
    ``create_cart``, and we lift the freshly-extracted snapshot at
    ``diagnostics.session_cart`` into a chat-renderable card so the user
    sees their cart receipt right under the assistant's confirmation
    bubble — no LLM signal required for the add-to-cart happy path.

    For *other* turns where the user asks about cart contents without
    triggering ``create_cart`` (e.g. *"what's in my cart?"*), the LLM-signal
    path in ``run_agent_turn`` handles emission from the carried-forward
    seed.
    """
    if not isinstance(raw_tool_results, dict):
        return []
    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        diag = raw.get("diagnostics")
        if not isinstance(diag, dict):
            continue
        if not diag.get("fresh_cart_captured"):
            continue
        snap = diag.get("session_cart")
        if not isinstance(snap, dict):
            continue
        att = _build_cart_card_attachment(snap, source=f"{provider_name}.create_cart")
        if att is not None:
            out.append(att)
    return out


# ---------------------------------------------------------------------------
# Swiggy-side card lifters.
#
# These mirror the Zomato shape contract (so the frontend renders the same
# three card kinds — ``restaurant_cards``, ``menu_cards``, ``cart_card`` —
# uniformly) but read from Swiggy's envelope shapes:
#
#   * search_restaurants → ``data.restaurants[]``
#   * search_menu        → ``data.items[]``
#   * get_restaurant_menu → ``data.categories[].items[]``
#   * update_food_cart / get_food_cart → executor diagnostics.session_cart
#
# Every attachment is stamped ``provider: "swiggy"`` so the chat-side
# components switch styling (Swiggy orange ``#FC8019`` vs Zomato red) and
# wire the right "Order now" / "Add to cart" prompt copy.
# ---------------------------------------------------------------------------
_SWIGGY_RESTAURANT_CARD_TOOLS = frozenset({"search_restaurants"})
_SWIGGY_MENU_CARD_TOOLS = frozenset({"search_menu", "get_restaurant_menu"})


def _build_swiggy_restaurant_card_entry(
    resto: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalise one Swiggy ``data.restaurants[]`` row into a card entry.

    Returns ``None`` for rows missing the basics (``id`` + ``name``).

    Source shape (from ``outputs/search_restaurants_*.json``)::

        {id, name, cuisines[], avgRating, totalRatings, costForTwo,
         areaName, distanceKm, deliveryTimeMinutes, deliveryTimeRange,
         offer, imageUrl, availabilityStatus}
    """
    if not isinstance(resto, dict):
        return None
    rid_raw = resto.get("id") or resto.get("restaurant_id") or resto.get("res_id")
    name = resto.get("name") or resto.get("restaurant_name")
    if rid_raw is None or not isinstance(name, str) or not name.strip():
        return None
    rid = str(rid_raw).strip()
    if not rid:
        return None
    out: dict[str, Any] = {"res_id": rid, "name": name.strip()}
    rating = resto.get("avgRating") or resto.get("rating")
    if isinstance(rating, (int, float)) and rating > 0:
        out["rating"] = round(float(rating), 1)
    votes = resto.get("totalRatings") or resto.get("votes")
    if isinstance(votes, str) and votes.strip():
        out["votes"] = votes.strip()
    elif isinstance(votes, (int, float)) and votes > 0:
        out["votes"] = int(votes)
    eta = (
        resto.get("deliveryTimeRange")
        or resto.get("eta")
        or (
            f"{resto.get('deliveryTimeMinutes')} MINS"
            if isinstance(resto.get("deliveryTimeMinutes"), (int, float))
            else None
        )
    )
    if isinstance(eta, str) and eta.strip():
        out["eta"] = eta.strip()
    dist = resto.get("distanceKm") or resto.get("distance_km") or resto.get("distance")
    if isinstance(dist, (int, float)) and dist >= 0:
        out["distance_km"] = round(float(dist), 2)
    image = resto.get("imageUrl") or resto.get("res_image") or resto.get("image_url")
    if isinstance(image, str) and image.strip():
        url = image.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://media-assets.swiggy.com/swiggy/image/upload/{url}"
        out["res_image"] = url
    offer = resto.get("offer") or resto.get("res_offer")
    if isinstance(offer, str) and offer.strip():
        out["res_offer"] = offer.strip()
    avail = resto.get("availabilityStatus") or resto.get("serviceability_status")
    if isinstance(avail, str) and avail.strip():
        out["serviceability_status"] = avail.strip()
    cuisines = resto.get("cuisines")
    if isinstance(cuisines, list):
        clean = [c.strip() for c in cuisines if isinstance(c, str) and c.strip()]
        if clean:
            out["cuisines"] = clean[:4]
    return out


def _collect_swiggy_restaurant_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Lift Swiggy ``search_restaurants`` envelopes into renderable cards.

    Mirrors the Zomato ``_collect_restaurant_attachments`` contract — same
    output shape (``kind: "restaurant_cards"`` with ``restaurants[]``) so
    the frontend's <RestaurantCardGrid /> can render either provider
    interchangeably. The ``provider: "swiggy"`` tag steers the visual
    branding (Swiggy orange instead of Zomato red).
    """
    if not isinstance(raw_tool_results, dict):
        return []
    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        if "swiggy" not in str(provider_name or "").lower():
            continue
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            if tool not in _SWIGGY_RESTAURANT_CARD_TOOLS:
                continue
            envelope = step.get("result")
            if not isinstance(envelope, dict) or envelope.get("is_error"):
                continue
            data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
            if not isinstance(data, dict):
                continue
            restos = data.get("restaurants")
            if not isinstance(restos, list) or not restos:
                continue
            cards: list[dict[str, Any]] = []
            seen: set[str] = set()
            for resto in restos:
                if len(cards) >= _MAX_RESTAURANT_CARDS:
                    break
                entry = _build_swiggy_restaurant_card_entry(resto)
                if entry is None:
                    continue
                if entry["res_id"] in seen:
                    continue
                seen.add(entry["res_id"])
                cards.append(entry)
            if not cards:
                continue
            attachment: dict[str, Any] = {
                "kind": "restaurant_cards",
                "source": f"{provider_name}.{tool}",
                "provider": "swiggy",
                "restaurants": cards,
            }
            args = step.get("arguments") or {}
            if isinstance(args, dict):
                keyword = args.get("query") or args.get("keyword")
                if isinstance(keyword, str) and keyword.strip():
                    attachment["keyword"] = keyword.strip()
            out.append(attachment)
    return out


def _build_swiggy_menu_card(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one Swiggy menu item into a renderable menu card.

    Source shapes:
      * ``search_menu`` items: ``{menu_item_id, name, price, isVeg,
        inStock, imageUrl, rating, totalRatings, hasVariants, hasAddons,
        variantsV2, addons}``
      * ``get_restaurant_menu`` items: ``{id, name, description, price,
        inStock, isVeg, imageUrl, hasVariants, hasAddons}``
    """
    if not isinstance(item, dict):
        return None
    iid_raw = (
        item.get("menu_item_id")
        or item.get("menuItemId")
        or item.get("id")
        or item.get("itemId")
    )
    name = item.get("name") or item.get("item_name")
    if iid_raw is None or not isinstance(name, str) or not name.strip():
        return None
    iid = str(iid_raw).strip()
    if not iid:
        return None
    card: dict[str, Any] = {
        "name": name.strip(),
        # Same id reused for both keys: the frontend reads ``variant_id``
        # first then falls back to ``item_id``, so we satisfy the
        # Zomato-shaped contract without inventing a separate id.
        "variant_id": iid,
        "item_id": iid,
    }
    price = item.get("price") or item.get("finalPrice")
    if isinstance(price, (int, float)) and price > 0:
        card["price"] = price
    image = item.get("imageUrl") or item.get("image_url")
    if isinstance(image, str) and image.strip():
        url = image.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://media-assets.swiggy.com/swiggy/image/upload/{url}"
        card["image_url"] = url
    rating = item.get("rating")
    if isinstance(rating, str):
        try:
            rating_f = float(rating)
            if rating_f > 0:
                card["rating"] = round(rating_f, 1)
        except ValueError:
            pass
    elif isinstance(rating, (int, float)) and rating > 0:
        card["rating"] = round(float(rating), 1)
    is_veg = item.get("isVeg")
    if isinstance(is_veg, bool):
        card["veg_kind"] = "veg" if is_veg else "non_veg"
    elif isinstance(is_veg, int):
        card["veg_kind"] = "veg" if is_veg == 1 else "non_veg"
    desc = item.get("description")
    if isinstance(desc, str) and desc.strip():
        d = desc.strip()
        card["description"] = (d[:140] + "…") if len(d) > 140 else d
    return card


def _iter_swiggy_menu_items(envelope: dict[str, Any]):
    """Yield raw menu-item dicts from a Swiggy menu envelope.

    Handles both shapes:
      * ``search_menu``: ``data.items[]``
      * ``get_restaurant_menu``: ``data.categories[].items[]``
    """
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return
    data = envelope.get("data")
    if not isinstance(data, dict):
        return
    items = data.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                yield it
    cats = data.get("categories") or data.get("menu_categories")
    if isinstance(cats, list):
        for cat in cats:
            if not isinstance(cat, dict):
                continue
            sub = cat.get("items")
            if isinstance(sub, list):
                for it in sub:
                    if isinstance(it, dict):
                        yield it


def _collect_swiggy_menu_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Lift Swiggy menu rows out of raw tool steps into renderable cards.

    Same output shape as the Zomato lifter — ``kind: "menu_cards"`` with
    ``items[]`` and ``restaurant_name`` — so the frontend's
    <MenuCardGrid /> can render either provider interchangeably.
    """
    if not isinstance(raw_tool_results, dict):
        return []
    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        if "swiggy" not in str(provider_name or "").lower():
            continue
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            if tool not in _SWIGGY_MENU_CARD_TOOLS:
                continue
            envelope = step.get("result")
            if not isinstance(envelope, dict) or envelope.get("is_error"):
                continue
            cards: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw_item in _iter_swiggy_menu_items(envelope):
                if len(cards) >= _MAX_MENU_CARDS:
                    break
                card = _build_swiggy_menu_card(raw_item)
                if card is None:
                    continue
                vid = card.get("variant_id")
                if isinstance(vid, str) and vid in seen:
                    continue
                if isinstance(vid, str):
                    seen.add(vid)
                cards.append(card)
            if not cards:
                continue
            attachment: dict[str, Any] = {
                "kind": "menu_cards",
                "source": f"{provider_name}.{tool}",
                "provider": "swiggy",
                "items": cards,
            }
            data = envelope.get("data") if isinstance(envelope.get("data"), dict) else None
            if isinstance(data, dict):
                rest = data.get("restaurant")
                if isinstance(rest, dict):
                    rname = rest.get("name") or rest.get("restaurantName")
                    if isinstance(rname, str) and rname.strip():
                        attachment["restaurant_name"] = rname.strip()
                    rid = rest.get("id") or rest.get("restaurant_id")
                    if rid not in (None, ""):
                        attachment["res_id"] = rid
            out.append(attachment)
    return out


# ---------------------------------------------------------------------------
# District attachment lifters.
#
# District has three discovery surfaces (dining / movies / events) plus a
# reservation snapshot. Each surface becomes its own attachment kind so the
# frontend can render the right card layout:
#
#   * search_dining_restaurants → ``dining_cards``
#   * search_movies             → ``movie_cards``
#   * search_theatres           → ``theatre_cards``
#   * search_shows              → ``show_cards``
#   * search_events             → ``event_cards``
#   * search_event_venues       → ``venue_cards``
#   * search_artists            → ``artist_cards``
#   * create_table_reservation_booking → ``reservation_card`` (auto-emit on
#     fresh booking; chat UI also shows it on follow-up turns via
#     session_reservation echo + a show_reservation LLM signal)
#
# Every attachment is stamped ``provider: "district"`` so the chat-side
# theme picker switches to District's branding.
# ---------------------------------------------------------------------------
_DISTRICT_MAX_CARDS = 8


def _district_envelope_data(envelope: dict[str, Any]) -> dict[str, Any] | None:
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if isinstance(data, str):
        try:
            import json as _json

            parsed = _json.loads(data)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def _build_district_dining_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    rid = item.get("res_id") or item.get("restaurant_id") or item.get("id")
    name = item.get("res_name") or item.get("name") or item.get("restaurant_name")
    if rid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"res_id": str(rid).strip(), "name": name.strip()}
    rating = item.get("rating") or item.get("avg_rating")
    if isinstance(rating, (int, float)) and rating > 0:
        out["rating"] = round(float(rating), 1)
    cuisines = item.get("cuisine") or item.get("cuisines")
    if isinstance(cuisines, list):
        clean = [c.strip() for c in cuisines if isinstance(c, str) and c.strip()]
        if clean:
            out["cuisines"] = clean[:4]
    elif isinstance(cuisines, str) and cuisines.strip():
        out["cuisines"] = [cuisines.strip()]
    locality = item.get("locality") or item.get("area")
    if isinstance(locality, str) and locality.strip():
        out["locality"] = locality.strip()
    price = item.get("price_for_two") or item.get("cost_for_two")
    if isinstance(price, (int, float, str)) and str(price).strip():
        out["price_for_two"] = price
    image = item.get("image_url") or item.get("photo_url")
    if isinstance(image, str) and image.strip():
        out["image_url"] = image.strip()
    bookable = item.get("bookable_via_agent")
    if isinstance(bookable, bool):
        out["bookable_via_agent"] = bookable
    distance = item.get("distance")
    if isinstance(distance, (int, float)) and distance >= 0:
        out["distance"] = round(float(distance), 2)
    elif isinstance(distance, str) and distance.strip():
        out["distance"] = distance.strip()
    web_url = item.get("web_url")
    if isinstance(web_url, str) and web_url.strip():
        out["web_url"] = web_url.strip()
    return out


def _build_district_movie_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    mid = item.get("movie_id") or item.get("id") or item.get("content_id")
    name = item.get("movie_name") or item.get("name") or item.get("title")
    if mid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"movie_id": str(mid).strip(), "name": name.strip()}
    lang = item.get("language") or item.get("languages")
    if isinstance(lang, list):
        clean = [v.strip() for v in lang if isinstance(v, str) and v.strip()]
        if clean:
            out["languages"] = clean[:4]
    elif isinstance(lang, str) and lang.strip():
        out["languages"] = [lang.strip()]
    genre = item.get("genre") or item.get("genres")
    if isinstance(genre, list):
        clean = [v.strip() for v in genre if isinstance(v, str) and v.strip()]
        if clean:
            out["genres"] = clean[:4]
    elif isinstance(genre, str) and genre.strip():
        out["genres"] = [genre.strip()]
    duration = item.get("duration_minutes") or item.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        out["duration_minutes"] = int(duration)
    rating = item.get("rating") or item.get("imdb_rating")
    if isinstance(rating, (int, float)) and rating > 0:
        out["rating"] = round(float(rating), 1)
    cert = item.get("cbfc_certification")
    if isinstance(cert, str) and cert.strip():
        out["cbfc_certification"] = cert.strip()
    release = item.get("release_date")
    if isinstance(release, str) and release.strip():
        out["release_date"] = release.strip()
    poster = (
        item.get("poster_url")
        or item.get("image_url")
        or item.get("spotlight_thumbnail_url")
    )
    if isinstance(poster, str) and poster.strip():
        out["poster_url"] = poster.strip()
    video = item.get("spotlight_video_url")
    if isinstance(video, str) and video.strip():
        out["spotlight_video_url"] = video.strip()
    web_url = item.get("web_url")
    if isinstance(web_url, str) and web_url.strip():
        out["web_url"] = web_url.strip()
    return out


def _build_district_theatre_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    tid = (
        item.get("theatre_id")
        or item.get("theater_id")
        or item.get("id")
        or item.get("cinema_id")
    )
    name = item.get("theatre_name") or item.get("name") or item.get("theater_name")
    if tid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"theatre_id": str(tid).strip(), "name": name.strip()}
    locality = item.get("locality") or item.get("area")
    if isinstance(locality, str) and locality.strip():
        out["locality"] = locality.strip()
    distance = item.get("distance")
    if isinstance(distance, (int, float)) and distance >= 0:
        out["distance"] = round(float(distance), 2)
    elif isinstance(distance, str) and distance.strip():
        out["distance"] = distance.strip()
    image = item.get("image_url") or item.get("photo_url")
    if isinstance(image, str) and image.strip():
        out["image_url"] = image.strip()
    web_url = item.get("web_url")
    if isinstance(web_url, str) and web_url.strip():
        out["web_url"] = web_url.strip()
    return out


def _build_district_show_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    sid = item.get("show_id") or item.get("id")
    if sid is None:
        return None
    out: dict[str, Any] = {"show_id": str(sid).strip()}
    for k_src, k_dst in (
        ("movie_id", "movie_id"),
        ("movie_name", "movie_name"),
        ("theatre_id", "theatre_id"),
        ("theatre_name", "theatre_name"),
        ("show_date", "show_date"),
        ("show_start_time", "show_start_time"),
        ("screen_type", "screen_type"),
        ("language", "language"),
        ("min_price", "min_price"),
        ("max_price", "max_price"),
        ("seat_availability", "seat_availability"),
        ("booking_url", "booking_url"),
        ("web_url", "web_url"),
    ):
        val = item.get(k_src)
        if val in (None, ""):
            continue
        if isinstance(val, str):
            vs = val.strip()
            if vs:
                out[k_dst] = vs
            continue
        out[k_dst] = val
    return out


def _build_district_event_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    eid = item.get("event_id") or item.get("id")
    name = item.get("name") or item.get("event_name") or item.get("title")
    if eid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"event_id": str(eid).strip(), "name": name.strip()}
    for k_src, k_dst in (
        ("description", "description"),
        ("date_string", "date_string"),
        ("venue_name", "venue_name"),
        ("locality", "locality"),
        ("city", "city"),
        ("price_string", "price_string"),
        ("offer_string", "offer_string"),
        ("image_url", "image_url"),
        ("video_url", "video_url"),
        ("web_url", "web_url"),
    ):
        val = item.get(k_src)
        if isinstance(val, str) and val.strip():
            out[k_dst] = val.strip()
    min_price = item.get("min_price")
    if isinstance(min_price, (int, float)) and min_price >= 0:
        out["min_price"] = float(min_price)
    distance = item.get("distance")
    if isinstance(distance, (int, float)) and distance >= 0:
        out["distance_km"] = round(float(distance), 2)
    tags = item.get("tags")
    if isinstance(tags, list):
        clean = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        if clean:
            out["tags"] = clean[:6]
    for flag in ("is_dining_event", "is_activity", "is_play", "is_attraction"):
        val = item.get(flag)
        if isinstance(val, bool) and val:
            out[flag] = True
    rating = item.get("rating_info")
    if isinstance(rating, dict):
        rv = rating.get("rating")
        if isinstance(rv, (int, float)) and rv > 0:
            out["rating"] = round(float(rv), 1)
        rc = rating.get("review_count")
        if isinstance(rc, (int, float)) and rc > 0:
            out["review_count"] = int(rc)
    return out


def _build_district_venue_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    vid = item.get("venue_id") or item.get("id")
    name = item.get("venue_name") or item.get("name")
    if vid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"venue_id": str(vid).strip(), "name": name.strip()}
    for k_src, k_dst in (
        ("city", "city"),
        ("locality", "locality"),
        ("image_url", "image_url"),
        ("web_url", "web_url"),
    ):
        val = item.get(k_src)
        if isinstance(val, str) and val.strip():
            out[k_dst] = val.strip()
    distance = item.get("distance")
    if isinstance(distance, (int, float)) and distance >= 0:
        out["distance_km"] = round(float(distance), 2)
    for k_src, k_dst in (("event_names", "event_names"), ("categories", "categories")):
        val = item.get(k_src)
        if isinstance(val, list):
            clean = [v.strip() for v in val if isinstance(v, str) and v.strip()]
            if clean:
                out[k_dst] = clean[:5]
    return out


def _build_district_artist_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    aid = item.get("artist_id") or item.get("id")
    name = item.get("artist_name") or item.get("name")
    if aid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"artist_id": str(aid).strip(), "name": name.strip()}
    a_type = item.get("artist_type")
    if isinstance(a_type, str) and a_type.strip():
        out["artist_type"] = a_type.strip()
    description = item.get("description")
    if isinstance(description, str) and description.strip():
        out["description"] = description.strip()
    image = item.get("image_url")
    if isinstance(image, str) and image.strip():
        out["image_url"] = image.strip()
    upcoming = item.get("upcoming_event_count")
    if isinstance(upcoming, (int, float)) and upcoming >= 0:
        out["upcoming_event_count"] = int(upcoming)
    genres = item.get("genres")
    if isinstance(genres, list):
        clean = [g.strip() for g in genres if isinstance(g, str) and g.strip()]
        if clean:
            out["genres"] = clean[:5]
    return out


_DISTRICT_CARD_BUILDERS = (
    # (tool name, list-key in envelope.data, builder fn, attachment kind,
    #  id key, attachment field name where cards go)
    # We use distinct attachment field names (e.g. ``dining_restaurants``,
    # NOT ``restaurants``) so the chat-side <Attachment> shape stays
    # unambiguous — the Zomato/Swiggy lifters already publish under
    # ``restaurants`` for keyword-search results, and we don't want a
    # dining card array to be silently rendered through that path.
    ("search_dining_restaurants", "restaurants", _build_district_dining_card,
     "dining_cards", "res_id", "dining_restaurants"),
    ("get_dining_restaurants_details", "restaurants", _build_district_dining_card,
     "dining_cards", "res_id", "dining_restaurants"),
    ("search_movies", "movies", _build_district_movie_card,
     "movie_cards", "movie_id", "movies"),
    ("search_theatres", "theatres", _build_district_theatre_card,
     "theatre_cards", "theatre_id", "theatres"),
    ("search_shows", "shows", _build_district_show_card,
     "show_cards", "show_id", "shows"),
    ("search_events", "events", _build_district_event_card,
     "event_cards", "event_id", "events"),
    ("search_event_venues", "venues", _build_district_venue_card,
     "venue_cards", "venue_id", "venues"),
    ("search_artists", "artists", _build_district_artist_card,
     "artist_cards", "artist_id", "artists"),
)


def _collect_district_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Walk every district step and lift discovery envelopes into typed card lists.

    Mirrors the Zomato / Swiggy lifter contract. The original envelope is
    left untouched so the response LLM, observable trace, and debug
    payloads still see the raw structure.
    """
    if not isinstance(raw_tool_results, dict):
        return []
    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        if "district" not in str(provider_name or "").lower():
            continue
        steps = raw.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            envelope = step.get("result")
            if not isinstance(envelope, dict) or envelope.get("is_error"):
                continue
            data = _district_envelope_data(envelope)
            if not isinstance(data, dict):
                continue
            matched = next(
                (cfg for cfg in _DISTRICT_CARD_BUILDERS if cfg[0] == tool), None
            )
            if matched is None:
                continue
            _tool, list_key, builder, kind, id_key, attach_field = matched
            rows = data.get(list_key)
            if not isinstance(rows, list) or not rows:
                continue
            cards: list[dict[str, Any]] = []
            seen: set[str] = set()
            for raw_row in rows:
                if len(cards) >= _DISTRICT_MAX_CARDS:
                    break
                card = builder(raw_row)
                if card is None:
                    continue
                ident = str(card.get(id_key) or "").strip()
                if ident and ident in seen:
                    continue
                if ident:
                    seen.add(ident)
                cards.append(card)
            if not cards:
                continue
            attachment: dict[str, Any] = {
                "kind": kind,
                "source": f"{provider_name}.{tool}",
                "provider": "district",
                attach_field: cards,
            }
            out.append(attachment)
    return out


def _build_district_reservation_attachment(
    snapshot: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    if not str(snapshot.get("booking_id") or "").strip():
        return None
    return {
        "kind": "reservation_card",
        "source": source,
        "provider": "district",
        "reservation": dict(snapshot),
    }


def _collect_district_reservation_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Emit a reservation card whenever the executor captured a fresh
    booking this turn (``diagnostics.fresh_reservation_captured``).

    Mirrors the Zomato cart-card auto-emit pattern. For follow-up turns
    where the user asks for status without triggering a booking call, the
    response LLM can ask the chat surface to show the reservation via a
    ``show_reservation`` signal (handled downstream from
    ``session_reservation``).
    """
    if not isinstance(raw_tool_results, dict):
        return []
    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        if "district" not in str(provider_name or "").lower():
            continue
        diag = raw.get("diagnostics")
        if not isinstance(diag, dict):
            continue
        if not diag.get("fresh_reservation_captured"):
            continue
        snap = diag.get("session_reservation")
        if not isinstance(snap, dict):
            continue
        att = _build_district_reservation_attachment(
            snap, source=f"{provider_name}.create_table_reservation_booking"
        )
        if att is not None:
            out.append(att)
    return out


# ---------------------------------------------------------------------------
# Zepto attachment builders
# ---------------------------------------------------------------------------
# Zepto search responses come back as formatted prose; the executor lifts
# each result into a normalised row in ``diagnostics.session_zepto_products``
# (the freshly-merged list always includes whatever this turn added). For
# UI rendering we want only the *newly added* rows from this turn, not the
# carried-forward LRU, so the chat doesn't re-show the same products on
# every follow-up message.
def _build_zepto_product_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    pvid = str(item.get("product_variant_id") or "").strip()
    spid = str(item.get("store_product_id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not pvid or not spid or not name:
        return None
    return {
        "product_variant_id": pvid,
        "store_product_id": spid,
        "name": name,
        "price_inr": item.get("price_inr"),
        "price_text": item.get("price_text"),
        "pack_size": item.get("pack_size"),
        "image_url": item.get("image_url"),
        "query": item.get("query"),
    }


def _collect_zepto_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build ``product_cards`` + ``zepto_cart_card`` attachments from the
    Zepto executor's diagnostics for this turn.

    Card emission rules:
      * Every ``search_products`` / ``search_multiple_products`` /
        ``get_past_order_items`` step that returned products → one
        ``product_cards`` attachment scoped to those rows.
      * Every ``view_cart`` / ``update_cart`` step → one
        ``zepto_cart_card`` reflecting the freshest cart snapshot the
        executor surfaced this turn (``diagnostics.session_zepto_cart``).
    """
    if not isinstance(raw_tool_results, dict):
        return []
    from app.services.zepto.mcp_executor import (
        _harvest_multi_products,
        _harvest_products,
    )

    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        if str(provider_name or "").lower() != "zepto":
            continue
        steps = raw.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip()
            envelope = step.get("result")
            if not isinstance(envelope, dict):
                continue
            if envelope.get("is_error"):
                continue
            if tool in {"search_products", "search_multiple_products"}:
                if tool == "search_products":
                    args = step.get("arguments") or {}
                    rows = _harvest_products(
                        envelope,
                        query=str((args or {}).get("query") or "").strip() or None,
                    )
                else:
                    rows = _harvest_multi_products(envelope)
                cards = [
                    c
                    for c in (_build_zepto_product_card(r) for r in rows)
                    if c is not None
                ]
                if cards:
                    out.append(
                        {
                            "kind": "zepto_product_cards",
                            "provider": "zepto",
                            "source": f"{provider_name}.{tool}",
                            "products": cards,
                        }
                    )
        # Cart card — one per turn, based on the merged session snapshot.
        diag = raw.get("diagnostics")
        if isinstance(diag, dict):
            cart_snap = diag.get("session_zepto_cart")
            if isinstance(cart_snap, dict):
                # Only emit a card when the executor actually ran a cart
                # tool this turn (otherwise we'd spam every turn with the
                # same cart UI). Detect via tool history.
                cart_touched = any(
                    isinstance(s, dict)
                    and str(s.get("tool") or "") in {"view_cart", "update_cart"}
                    for s in (raw.get("steps") or [])
                )
                if cart_touched:
                    out.append(
                        {
                            "kind": "zepto_cart_card",
                            "provider": "zepto",
                            "source": f"{provider_name}.view_cart",
                            "zepto_cart": dict(cart_snap),
                        }
                    )
    return out


# ---------------------------------------------------------------------------
# Instamart attachment builders
# ---------------------------------------------------------------------------
# Mirrors ``_collect_zepto_attachments``. Instamart's executor already
# harvests ``session_instamart_*`` into diagnostics; here we shape per-turn
# attachments so the UI gets product grids on every search and a cart card
# whenever a cart tool ran.
def _build_instamart_product_card(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    spin_id = str(item.get("spin_id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not spin_id or not name:
        return None
    return {
        "spin_id": spin_id,
        "product_id": item.get("product_id"),
        "parent_product_id": item.get("parent_product_id"),
        "name": name,
        "brand": item.get("brand"),
        "quantity_description": item.get("quantity_description"),
        "price_inr": item.get("price_inr"),
        "mrp_inr": item.get("mrp_inr"),
        "image_url": item.get("image_url"),
        "in_stock": item.get("in_stock", True),
        "is_promoted": item.get("is_promoted", False),
        "query": item.get("query"),
    }


def _collect_instamart_attachments(
    raw_tool_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build product-grid + cart-card attachments from the Instamart executor."""
    if not isinstance(raw_tool_results, dict):
        return []
    from app.services.instamart.mcp_executor import (
        _instamart_products_from_search,
    )

    out: list[dict[str, Any]] = []
    for provider_name, raw in raw_tool_results.items():
        if not isinstance(raw, dict):
            continue
        if str(provider_name or "").lower() != "swiggy_instamart":
            continue
        steps = raw.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool = str(step.get("tool") or "").strip().split("(", 1)[0]
            envelope = step.get("result")
            if not isinstance(envelope, dict):
                continue
            if envelope.get("is_error"):
                continue
            if tool in {"search_products", "your_go_to_items"}:
                args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
                query = str((args or {}).get("query") or "").strip() or None
                rows = _instamart_products_from_search(envelope, query=query)
                cards = [
                    c for c in (_build_instamart_product_card(r) for r in rows) if c is not None
                ]
                if cards:
                    out.append(
                        {
                            "kind": "instamart_product_cards",
                            "provider": "swiggy_instamart",
                            "source": f"{provider_name}.{tool}",
                            "products": cards,
                        }
                    )
        # Cart card — one per turn, based on the merged session snapshot.
        diag = raw.get("diagnostics")
        if isinstance(diag, dict):
            cart_snap = diag.get("session_instamart_cart")
            if isinstance(cart_snap, dict):
                cart_touched = any(
                    isinstance(s, dict)
                    and str(s.get("tool") or "").split("(", 1)[0]
                    in {"get_cart", "update_cart", "clear_cart"}
                    for s in (raw.get("steps") or [])
                )
                if cart_touched:
                    out.append(
                        {
                            "kind": "instamart_cart_card",
                            "provider": "swiggy_instamart",
                            "source": f"{provider_name}.get_cart",
                            "instamart_cart": dict(cart_snap),
                        }
                    )
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
    session_events: list[dict[str, Any]] | None = None,
    session_venues: list[dict[str, Any]] | None = None,
    session_artists: list[dict[str, Any]] | None = None,
    session_reservation: dict[str, Any] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
    session_zepto_products: list[dict[str, Any]] | None = None,
    session_zepto_addresses: list[dict[str, Any]] | None = None,
    session_zepto_cart: dict[str, Any] | None = None,
    session_instamart_products: list[dict[str, Any]] | None = None,
    session_instamart_addresses: list[dict[str, Any]] | None = None,
    session_instamart_cart: dict[str, Any] | None = None,
    selected_address_id: str | None = None,
    pending_variant_choice: list[dict[str, Any]] | None = None,
    user_profile: dict[str, Any] | None = None,
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
    if session_events:
        event_rows: list[str] = []
        for entry in session_events[:10]:
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get("event_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if eid and name:
                date_hint = str(entry.get("date_string") or "").strip()
                tail = f" [{date_hint}]" if date_hint else ""
                event_rows.append(f"  - {name} → event_id={eid}{tail}")
        if event_rows:
            parts.append(
                "Events already shown this session — use literal event_id "
                "below for venue/show follow-ups instead of re-searching:\n"
                + "\n".join(event_rows)
            )
    if session_venues:
        venue_rows: list[str] = []
        for entry in session_venues[:10]:
            if not isinstance(entry, dict):
                continue
            vid = str(entry.get("venue_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if vid and name:
                venue_rows.append(f"  - {name} → venue_id={vid}")
        if venue_rows:
            parts.append(
                "Event venues already shown this session — use literal venue_id "
                "below instead of re-searching:\n" + "\n".join(venue_rows)
            )
    if session_artists:
        artist_rows: list[str] = []
        for entry in session_artists[:10]:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("artist_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if aid and name:
                artist_rows.append(f"  - {name} → artist_id={aid}")
        if artist_rows:
            parts.append(
                "Artists already shown this session — use literal artist_id "
                "below instead of re-searching:\n" + "\n".join(artist_rows)
            )
    if session_reservation and isinstance(session_reservation, dict):
        bid = str(session_reservation.get("booking_id") or "").strip()
        if bid:
            who = (
                str(session_reservation.get("restaurant_name") or "").strip()
                or "the restaurant"
            )
            when_bits = [
                str(session_reservation.get("booking_date") or "").strip(),
                str(session_reservation.get("slot_time") or "").strip(),
            ]
            when = " ".join(b for b in when_bits if b).strip() or "the chosen slot"
            party = session_reservation.get("party_size")
            party_hint = f" for {party}" if isinstance(party, int) and party > 0 else ""
            parts.append(
                "Active District reservation in session — use booking_id "
                f"{bid} (__BOOKING_ID__) when the user asks for status / "
                f"cancellation; details: {who} at {when}{party_hint}."
            )
    if session_menu:
        # Surface the recently-shown menu items so the planner can emit
        # ``create_cart`` / ``update_food_cart`` directly when the user
        # picks one (no redundant menu fetch). Both Zomato and Swiggy
        # entries render in this single block — provider is auto-detected
        # from the shape (Swiggy entries carry ``variantsV2`` / ``addons``
        # response-shape blocks, Zomato entries carry ``variants`` /
        # ``addon_groups`` flattened blocks).
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
            # ----- Zomato shape: flattened variants[] + addon_groups[] -----
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
            # ----- Swiggy shape: response-shape variantsV2 + addons -----
            v2 = entry.get("variantsV2") or []
            if isinstance(v2, list) and v2:
                # Compact: "Size: Medium=g1/v1, Large=g1/v2 | Crust: Hand-tossed=g2/v3"
                group_tags: list[str] = []
                for grp in v2[:4]:
                    if not isinstance(grp, dict):
                        continue
                    gid = str(grp.get("groupId") or grp.get("group_id") or "").strip()
                    gname = str(grp.get("name") or "").strip() or gid
                    choices: list[str] = []
                    for vrow in (grp.get("variations") or [])[:6]:
                        if not isinstance(vrow, dict):
                            continue
                        if vrow.get("inStock", 1) not in (1, True):
                            continue
                        vid2 = str(vrow.get("id") or "").strip()
                        vname = str(vrow.get("name") or "").strip()
                        if not vid2:
                            continue
                        choices.append(f"{vname or vid2}={gid}/{vid2}")
                    if choices:
                        group_tags.append(f"{gname}({','.join(choices)})")
                if group_tags:
                    tail_bits.append("variantsV2: " + " | ".join(group_tags))
            addons = entry.get("addons") or []
            if isinstance(addons, list) and addons:
                a_tags: list[str] = []
                for grp in addons[:4]:
                    if not isinstance(grp, dict):
                        continue
                    gid = str(grp.get("groupId") or grp.get("group_id") or "").strip()
                    gname = str(grp.get("name") or "").strip() or gid
                    mn = grp.get("minAddons") or grp.get("min_addons") or grp.get("min") or 0
                    mx = grp.get("maxAddons") or grp.get("max_addons") or grp.get("max") or 0
                    choices = []
                    for ch in (grp.get("choices") or grp.get("addonChoices") or [])[:5]:
                        if not isinstance(ch, dict):
                            continue
                        cid = str(ch.get("id") or ch.get("choice_id") or "").strip()
                        cname = str(ch.get("name") or "").strip()
                        if cid:
                            choices.append(f"{cname or cid}={gid}/{cid}")
                    cstr = (", ".join(choices)) if choices else ""
                    a_tags.append(f"{gname} (min={mn}, max={mx}){' → ' + cstr if cstr else ''}")
                if a_tags:
                    tail_bits.append("addons: " + " | ".join(a_tags))
            tail = f" [{' | '.join(tail_bits)}]" if tail_bits else ""
            # Label-switch: use ``menu_item_id=`` for Swiggy entries (any
            # ``variantsV2`` / ``hasVariants`` / ``hasAddons`` signal) so
            # the planner reads the right field name for its cart shape.
            id_label = (
                "menu_item_id"
                if (
                    entry.get("variantsV2")
                    or entry.get("hasVariants") is not None
                    or entry.get("hasAddons") is not None
                    or entry.get("menu_item_id")
                )
                else "variant_id"
            )
            menu_rows.append(f"  - {iname} → {id_label}={vid}{price}{tail}")
        if menu_rows:
            parts.append(
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
                "shown` for the item's outlet.\n"
                + "\n".join(menu_rows)
            )
    if session_zepto_products:
        # Surface the most-recently-shown Zepto products with their
        # ``pvid`` + ``spid`` ids so the planner can call ``update_cart``
        # directly when the user picks one by name. Zepto search returns
        # a different pvid every time we re-search even for the same
        # product, so re-searching is both wasteful and breaks cart adds.
        product_rows: list[str] = []
        for entry in session_zepto_products[:12]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            pvid = str(entry.get("product_variant_id") or "").strip()
            spid = str(entry.get("store_product_id") or "").strip()
            if not name or not pvid or not spid:
                continue
            price = entry.get("price_text") or (
                f"₹{entry.get('price_inr')}" if entry.get("price_inr") else None
            )
            pack = entry.get("pack_size")
            tail_bits = []
            if pack:
                tail_bits.append(str(pack))
            if price:
                tail_bits.append(str(price))
            tail = f" — {' / '.join(tail_bits)}" if tail_bits else ""
            product_rows.append(
                f"  - {name}{tail} → pvid={pvid}, spid={spid}"
            )
        if product_rows:
            parts.append(
                "Zepto products already shown to the user this session — "
                "when the user picks one by name (or asks to add it / "
                "remove it), copy the literal pvid + spid below into "
                "`update_cart.cartItems[]` instead of re-running "
                "`search_products` (a fresh search returns a different "
                "pvid for the same product, which silently breaks the "
                "cart flow):\n" + "\n".join(product_rows)
            )
    if session_zepto_addresses:
        zaddr_rows: list[str] = []
        for entry in session_zepto_addresses[:8]:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("address_id") or "").strip()
            label = str(entry.get("label") or "").strip()
            if not aid or not label:
                continue
            line = str(entry.get("address_line") or "").strip()
            tail = f" ({line[:80]})" if line else ""
            zaddr_rows.append(f"  - {label} → addressId={aid}{tail}")
        if zaddr_rows:
            parts.append(
                "Zepto saved addresses available this session — when the "
                "user asks to switch delivery address, copy the literal "
                "addressId below into `select_saved_address`:\n"
                + "\n".join(zaddr_rows)
            )
    if session_zepto_cart and isinstance(session_zepto_cart, dict):
        items = session_zepto_cart.get("items") or []
        if isinstance(items, list) and items:
            cart_lines: list[str] = []
            for it in items[:10]:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "").strip() or "(item)"
                qty = it.get("quantity") or 1
                pvid = str(it.get("product_variant_id") or "").strip()
                spid = str(it.get("store_product_id") or "").strip()
                if not pvid or not spid:
                    continue
                cart_lines.append(
                    f"  - {nm} ×{qty} → pvid={pvid}, spid={spid}"
                )
            if cart_lines:
                parts.append(
                    "Current Zepto cart (echoes server state from the "
                    "last `view_cart` or optimistic projection from "
                    "`update_cart`) — set `quantity: 0` for these "
                    "pvid/spid pairs to remove items, or any positive "
                    "number to set the new quantity:\n"
                    + "\n".join(cart_lines)
                )
        elif session_zepto_cart.get("is_empty"):
            parts.append("Current Zepto cart: empty.")

    # Instamart echoes — same contract as Zepto. The product index is keyed
    # on ``spin_id`` (Instamart's per-variant cart key); the planner copies
    # those literally into ``update_cart.items[]`` instead of re-searching.
    if session_instamart_products:
        im_prod_rows: list[str] = []
        for entry in session_instamart_products[:15]:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            spin = str(entry.get("spin_id") or "").strip()
            if not name or not spin:
                continue
            price = entry.get("price_inr")
            pack = entry.get("quantity_description")
            tail_bits = []
            if pack:
                tail_bits.append(str(pack))
            if isinstance(price, (int, float)):
                tail_bits.append(f"₹{price:g}")
            tail = f" — {' / '.join(tail_bits)}" if tail_bits else ""
            im_prod_rows.append(f"  - {name}{tail} → spinId={spin}")
        if im_prod_rows:
            parts.append(
                "Instamart products already shown to the user this session — "
                "when the user picks one by name (or asks to add it / change "
                "qty / remove it), copy the literal spinId below into "
                "`update_cart.items[]` instead of re-running `search_products`. "
                "Each row is one variant (pack size) — the user must explicitly "
                "pick a variant before any add-to-cart:\n" + "\n".join(im_prod_rows)
            )
    if session_instamart_addresses:
        im_addr_rows: list[str] = []
        for entry in session_instamart_addresses[:8]:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("address_id") or "").strip()
            label = str(entry.get("label") or "").strip() or str(entry.get("category") or "").strip()
            if not aid:
                continue
            line = str(entry.get("address_line") or "").strip()
            tail = f" ({line[:80]})" if line else ""
            head = label or "(unlabelled)"
            im_addr_rows.append(f"  - {head} → addressId={aid}{tail}")
        if im_addr_rows:
            parts.append(
                "Instamart saved addresses available this session — when the "
                "user asks to switch delivery address, copy the literal "
                "addressId below; the executor will substitute it into "
                "`__ADDRESS_ID__` automatically:\n" + "\n".join(im_addr_rows)
            )
    if session_instamart_cart and isinstance(session_instamart_cart, dict):
        im_items = session_instamart_cart.get("items") or []
        if isinstance(im_items, list) and im_items:
            im_cart_lines: list[str] = []
            for it in im_items[:10]:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "").strip() or "(item)"
                qty = it.get("quantity") or 1
                spin = str(it.get("spin_id") or "").strip()
                if not spin:
                    continue
                im_cart_lines.append(f"  - {nm} ×{qty} → spinId={spin}")
            if im_cart_lines:
                total = session_instamart_cart.get("grand_total_paise")
                total_line = (
                    f" (grand total: ₹{total/100:.0f})"
                    if isinstance(total, (int, float)) and total
                    else ""
                )
                parts.append(
                    "Current Instamart cart (last `get_cart` snapshot)"
                    + total_line
                    + " — Instamart `update_cart` is REPLACE-ALL, so to add a "
                    "new item or change qty you must re-list every spinId "
                    "below with the desired quantity (omit a line, or use "
                    "`clear_cart`, to remove):\n"
                    + "\n".join(im_cart_lines)
                )
        elif session_instamart_cart.get("is_empty"):
            parts.append("Current Instamart cart: empty.")

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

    if user_profile:
        profile_parts = []
        name = user_profile.get("name")
        age = user_profile.get("age")
        city = user_profile.get("city")
        lang = user_profile.get("language")

        basic = []
        if name: basic.append(f"Name: {name}")
        if age: basic.append(f"Age: {age}")
        if city: basic.append(f"City: {city}")
        if lang: basic.append(f"Language: {lang}")
        if basic:
            profile_parts.append("Basic Info: " + ", ".join(basic))

        addresses = user_profile.get("addresses", {})
        if addresses:
            addr_lines = []
            for label, details in addresses.items():
                if isinstance(details, dict) and details.get("address_line"):
                    line = f"  - {label.capitalize()}: {details.get('address_line')}"
                    if details.get("provider_address_id"):
                        line += f" (provider_address_id={details.get('provider_address_id')})"
                    addr_lines.append(line)
            if addr_lines:
                profile_parts.append("Saved addresses in user profile:\n" + "\n".join(addr_lines))

        interests = user_profile.get("interests", [])
        if interests:
            interest_lines = []
            for item in interests[:5]:
                cat = item.get("category")
                details = item.get("details", [])
                if cat:
                    detail_str = f" ({', '.join(details)})" if details else ""
                    interest_lines.append(f"  - {cat}{detail_str} (weight: {item.get('weight', 0.5)})")
            if interest_lines:
                profile_parts.append("User Interests:\n" + "\n".join(interest_lines))

        open_leads = user_profile.get("open_leads", [])
        if open_leads:
            lead_lines = []
            for lead in open_leads[:5]:
                prod = lead.get("product")
                cat = lead.get("category")
                if prod:
                    lead_lines.append(f"  - {prod} [{cat}] (intent_score: {lead.get('intent_score', 0.5)})")
            if lead_lines:
                profile_parts.append("Active Leads / Research Topics:\n" + "\n".join(lead_lines))

        if profile_parts:
            parts.append("User Digital Profile (durable facts & interests from history):\n" + "\n\n".join(profile_parts))

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
    if provider_id == "zepto":
        from app.services.zepto.planner import plan_zepto

        out = plan_zepto(user_message=routing_message, llm=llm)
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
    session_events: list[dict[str, Any]] | None = None,
    session_venues: list[dict[str, Any]] | None = None,
    session_artists: list[dict[str, Any]] | None = None,
    session_reservation: dict[str, Any] | None = None,
    session_menu: list[dict[str, Any]] | None = None,
    session_cart: dict[str, Any] | None = None,
    session_zepto_products: list[dict[str, Any]] | None = None,
    session_zepto_addresses: list[dict[str, Any]] | None = None,
    session_zepto_cart: dict[str, Any] | None = None,
    session_instamart_products: list[dict[str, Any]] | None = None,
    session_instamart_addresses: list[dict[str, Any]] | None = None,
    session_instamart_cart: dict[str, Any] | None = None,
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

    def _seed_index(
        raw: Any, *, id_key: str
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                ident = entry.get(id_key)
                name = entry.get("name")
                if (
                    isinstance(ident, str)
                    and ident.strip()
                    and isinstance(name, str)
                    and name.strip()
                ):
                    out.append({**entry, id_key: ident.strip(), "name": name.strip()})
        return out

    seed_events = _seed_index(session_events, id_key="event_id")
    seed_venues = _seed_index(session_venues, id_key="venue_id")
    seed_artists = _seed_index(session_artists, id_key="artist_id")

    seed_reservation: dict[str, Any] | None = None
    if isinstance(session_reservation, dict):
        bid = session_reservation.get("booking_id")
        if isinstance(bid, str) and bid.strip():
            seed_reservation = {**session_reservation, "booking_id": bid.strip()}

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

    # ``session_cart`` carry-forward — single dict (not LRU) because Zomato
    # only ever has one active cart per session. Must have ``cart_id`` and
    # at least one item; otherwise treated as missing and dropped.
    seed_cart: dict[str, Any] | None = None
    if isinstance(session_cart, dict):
        cid = session_cart.get("cart_id")
        items = session_cart.get("items")
        if isinstance(cid, str) and cid.strip() and isinstance(items, list) and items:
            seed_cart = {**session_cart, "cart_id": cid.strip()}

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

    # Zepto session seeds (products, saved addresses, cart). Same echo
    # contract as the other providers — the frontend round-trips each
    # field verbatim, the executor merges its new harvests on top, and
    # the response envelope ships the merged result back out.
    seed_zepto_products: list[dict[str, Any]] = []
    if isinstance(session_zepto_products, list):
        for entry in session_zepto_products:
            if not isinstance(entry, dict):
                continue
            pvid = entry.get("product_variant_id")
            spid = entry.get("store_product_id")
            name = entry.get("name")
            if (
                isinstance(pvid, str)
                and pvid.strip()
                and isinstance(spid, str)
                and spid.strip()
                and isinstance(name, str)
                and name.strip()
            ):
                seed_zepto_products.append(
                    {
                        **entry,
                        "product_variant_id": pvid.strip(),
                        "store_product_id": spid.strip(),
                        "name": name.strip(),
                    }
                )

    seed_zepto_addresses: list[dict[str, Any]] = []
    if isinstance(session_zepto_addresses, list):
        for entry in session_zepto_addresses:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id")
            label = entry.get("label")
            if (
                isinstance(aid, str)
                and aid.strip()
                and isinstance(label, str)
                and label.strip()
            ):
                seed_zepto_addresses.append(
                    {**entry, "address_id": aid.strip(), "label": label.strip()}
                )

    seed_zepto_cart: dict[str, Any] | None = None
    if isinstance(session_zepto_cart, dict):
        # Cart snapshot is single dict, but echo it back even when items is
        # an empty list (so the frontend can render "empty cart" UI).
        items = session_zepto_cart.get("items")
        if (isinstance(items, list)) or session_zepto_cart.get("is_empty"):
            seed_zepto_cart = dict(session_zepto_cart)

    # Instamart seeds — same contract as Zepto. Products keyed on ``spin_id``.
    seed_instamart_products: list[dict[str, Any]] = []
    if isinstance(session_instamart_products, list):
        for entry in session_instamart_products:
            if not isinstance(entry, dict):
                continue
            spin = entry.get("spin_id")
            name = entry.get("name")
            if (
                isinstance(spin, str)
                and spin.strip()
                and isinstance(name, str)
                and name.strip()
            ):
                seed_instamart_products.append(
                    {**entry, "spin_id": spin.strip(), "name": name.strip()}
                )

    seed_instamart_addresses: list[dict[str, Any]] = []
    if isinstance(session_instamart_addresses, list):
        for entry in session_instamart_addresses:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id")
            if isinstance(aid, str) and aid.strip():
                seed_instamart_addresses.append({**entry, "address_id": aid.strip()})

    seed_instamart_cart: dict[str, Any] | None = None
    if isinstance(session_instamart_cart, dict):
        im_items = session_instamart_cart.get("items")
        if isinstance(im_items, list) or session_instamart_cart.get("is_empty"):
            seed_instamart_cart = dict(session_instamart_cart)

    # ── Phase 2 Profile Personalization Hook ───────────────────────
    from companion_api.profile_cache import ProfileCacheRepository
    profile: dict[str, Any] | None = None
    profile_resolved = False
    try:
        profile = ProfileCacheRepository().get(user_id)
    except Exception as e:
        trace("profile_cache.error", error=str(e))

    # Seed session addresses from profile cache if not already present.
    # IMPORTANT: only seed addresses that carry a REAL provider_address_id.
    # A free-text address_line ("Sector 48, Gurgaon") is NOT a valid Zomato/
    # Swiggy address id — seeding it makes the provider search a bogus
    # location and return "no restaurants" (and overrides the real browser
    # location for text chat). Text-only profile addresses are instead turned
    # into coordinates (user_geo) just below.
    if profile and isinstance(profile.get("addresses"), dict):
        profile_addrs = profile.get("addresses", {})
        for label, details in profile_addrs.items():
            if not isinstance(details, dict):
                continue
            aid = details.get("provider_address_id")
            if not aid:
                continue
            exists = any(str(entry.get("address_id")).strip() == str(aid).strip() for entry in seed_addresses)
            if not exists:
                seed_addresses.append({
                    "address_id": aid,
                    "label": label,
                    "address_line": details.get("address_line"),
                    "lat": details.get("lat"),
                    "lng": details.get("lng"),
                    "provider_address_id": details.get("provider_address_id")
                })

    # Resolve address from message using profile facts
    resolved_addr_id = None
    resolved_label = None
    if profile and not address_id:
        text_lower = user_message.lower()
        addresses = profile.get("addresses", {})
        if "office" in text_lower or "work" in text_lower:
            office = addresses.get("office")
            if isinstance(office, dict):
                # Only a real provider_address_id is usable as an address id;
                # a text address_line is not (coords are derived above instead).
                resolved_addr_id = office.get("provider_address_id")
                resolved_label = "office"
        elif "home" in text_lower:
            home = addresses.get("home")
            if isinstance(home, dict):
                resolved_addr_id = home.get("provider_address_id")
                resolved_label = "home"
                
        if resolved_addr_id:
            address_id = resolved_addr_id
            profile_resolved = True
            trace("profile_cache.address_resolved", label=resolved_label, address_id=address_id)

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
    routing_message = _format_routing_message(user_message, conversation_history, user_profile=profile)
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
        session_events=seed_events or None,
        session_venues=seed_venues or None,
        session_artists=seed_artists or None,
        session_reservation=seed_reservation,
        session_menu=seed_menu or None,
        session_zepto_products=seed_zepto_products or None,
        session_zepto_addresses=seed_zepto_addresses or None,
        session_zepto_cart=seed_zepto_cart,
        session_instamart_products=seed_instamart_products or None,
        session_instamart_addresses=seed_instamart_addresses or None,
        session_instamart_cart=seed_instamart_cart,
        selected_address_id=selected_addr,
        pending_variant_choice=seed_variant_choices or None,
        user_profile=profile,
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
    elif task_type == "cab_booking":
        routed = set(orchestration.get("mobility_providers") or ["uber"])
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
    elif task_type == "cab_booking":
        routed = {"uber"}
        plan = plan_uber_cab(
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
    uber_execution: dict[str, Any] | None = None
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
    elif task_type == "cab_booking" and execute_tools:
        uber_execution = execute_uber_cab(
            plan=plan,
            user_id=user_id,
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
            session_events=seed_events or None,
            session_venues=seed_venues or None,
            session_artists=seed_artists or None,
            session_reservation=seed_reservation,
            session_menu=seed_menu or None,
            session_cart=seed_cart,
            session_zepto_products=seed_zepto_products or None,
            session_zepto_addresses=seed_zepto_addresses or None,
            session_zepto_cart=seed_zepto_cart,
            session_instamart_products=seed_instamart_products or None,
            session_instamart_addresses=seed_instamart_addresses or None,
            session_instamart_cart=seed_instamart_cart,
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
        else uber_execution
        if task_type == "cab_booking"
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
    swiggy_menu_attachments = _collect_swiggy_menu_attachments(raw_tool_results)
    if swiggy_menu_attachments:
        attachments = attachments + swiggy_menu_attachments
    restaurant_attachments = _collect_restaurant_attachments(raw_tool_results)
    if restaurant_attachments:
        attachments = attachments + restaurant_attachments
    swiggy_restaurant_attachments = _collect_swiggy_restaurant_attachments(raw_tool_results)
    if swiggy_restaurant_attachments:
        attachments = attachments + swiggy_restaurant_attachments
    cart_attachments = _collect_cart_attachments(raw_tool_results)
    if cart_attachments:
        attachments = attachments + cart_attachments
    district_attachments = _collect_district_attachments(raw_tool_results)
    if district_attachments:
        attachments = attachments + district_attachments
    district_reservation_attachments = _collect_district_reservation_attachments(
        raw_tool_results
    )
    if district_reservation_attachments:
        attachments = attachments + district_reservation_attachments
    zepto_attachments = _collect_zepto_attachments(raw_tool_results)
    if zepto_attachments:
        attachments = attachments + zepto_attachments
    instamart_attachments = _collect_instamart_attachments(raw_tool_results)
    if instamart_attachments:
        attachments = attachments + instamart_attachments
    trace(
        "hoister.out",
        attachment_count=len(attachments),
        payment_attachments=(
            len(attachments)
            - len(menu_attachments)
            - len(swiggy_menu_attachments)
            - len(restaurant_attachments)
            - len(swiggy_restaurant_attachments)
            - len(cart_attachments)
            - len(district_attachments)
            - len(district_reservation_attachments)
            - len(zepto_attachments)
        ),
        menu_attachments=len(menu_attachments) + len(swiggy_menu_attachments),
        restaurant_attachments=(
            len(restaurant_attachments) + len(swiggy_restaurant_attachments)
        ),
        cart_attachments=len(cart_attachments),
        district_attachments=len(district_attachments),
        district_reservation_attachments=len(district_reservation_attachments),
        zepto_attachments=len(zepto_attachments),
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
    if address_id and "__ADDRESS_ID__" not in next_session_state:
        next_session_state["__ADDRESS_ID__"] = str(address_id).strip()
    # Start the next-turn restaurant index from the seed; the executor's
    # diagnostics will override it if the current turn ran a search.
    next_session_restaurants: list[dict[str, Any]] = list(seed_restaurants)
    next_session_movies: list[dict[str, Any]] = list(seed_movies)
    next_session_theatres: list[dict[str, Any]] = list(seed_theatres)
    next_session_events: list[dict[str, Any]] = list(seed_events)
    next_session_venues: list[dict[str, Any]] = list(seed_venues)
    next_session_artists: list[dict[str, Any]] = list(seed_artists)
    next_session_reservation: dict[str, Any] | None = seed_reservation
    # Same pattern for the per-item LRU — seed-first, then overwrite with
    # whatever the executor emitted (richness-aware merge already done
    # inside the executor).
    next_session_menu: list[dict[str, Any]] = list(seed_menu)
    next_session_cart: dict[str, Any] | None = seed_cart
    # Track which provider produced the cart this turn — so the LLM-signal
    # ``show_cart`` branch downstream stamps the right ``provider`` on the
    # emitted ``cart_card`` (and the frontend renders Zomato-red vs Swiggy-
    # orange branding + the right "Order now" prompt).
    next_session_cart_provider: str | None = None
    # Same pattern for saved addresses — if the executor (re)fetched the
    # list this turn, its result wins; otherwise we just echo the seed
    # back so the frontend can keep persisting it.
    next_session_addresses: list[dict[str, Any]] = list(seed_addresses)
    # Zepto echoes: same seed-first / executor-overrides pattern.
    next_session_zepto_products: list[dict[str, Any]] = list(seed_zepto_products)
    next_session_zepto_addresses: list[dict[str, Any]] = list(seed_zepto_addresses)
    next_session_zepto_cart: dict[str, Any] | None = (
        dict(seed_zepto_cart) if isinstance(seed_zepto_cart, dict) else None
    )
    # Instamart echoes — same seed-first / executor-overrides pattern.
    next_session_instamart_products: list[dict[str, Any]] = list(seed_instamart_products)
    next_session_instamart_addresses: list[dict[str, Any]] = list(seed_instamart_addresses)
    next_session_instamart_cart: dict[str, Any] | None = (
        dict(seed_instamart_cart) if isinstance(seed_instamart_cart, dict) else None
    )
    # ``pending_variant_choice`` is single-turn state: if the executor
    # deferred a cart this turn we echo it back so the next turn's
    # planner can resolve the user's pick. If the executor did NOT
    # defer (turn succeeded or never tried), we DO NOT echo it back —
    # the choice was either satisfied or no longer relevant.
    next_pending_variant_choice: list[dict[str, Any]] = []
    address_source: str | None = "profile" if profile_resolved else None
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
        # District event-surface indexes — same merge pattern: executor's list
        # is authoritative (most-recent at the front, deduped by id).
        for _diag_key, _next_var_name, _id_key in (
            ("session_events", "events", "event_id"),
            ("session_venues", "venues", "venue_id"),
            ("session_artists", "artists", "artist_id"),
        ):
            _raw = _diag.get(_diag_key)
            if not isinstance(_raw, list) or not _raw:
                continue
            cleaned_idx: list[dict[str, Any]] = []
            for entry in _raw:
                if not isinstance(entry, dict):
                    continue
                ident = entry.get(_id_key)
                name = entry.get("name")
                if (
                    isinstance(ident, str)
                    and ident.strip()
                    and isinstance(name, str)
                    and name.strip()
                ):
                    cleaned_idx.append(
                        {**entry, _id_key: ident.strip(), "name": name.strip()}
                    )
            if cleaned_idx:
                if _next_var_name == "events":
                    next_session_events = cleaned_idx
                elif _next_var_name == "venues":
                    next_session_venues = cleaned_idx
                elif _next_var_name == "artists":
                    next_session_artists = cleaned_idx
        _reservation = _diag.get("session_reservation")
        if isinstance(_reservation, dict):
            _bid = _reservation.get("booking_id")
            if isinstance(_bid, str) and _bid.strip():
                next_session_reservation = {**_reservation, "booking_id": _bid.strip()}
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
        _cart = _diag.get("session_cart")
        if isinstance(_cart, dict):
            _cid = _cart.get("cart_id")
            _citems = _cart.get("items")
            if isinstance(_cid, str) and _cid.strip() and isinstance(_citems, list) and _citems:
                next_session_cart = {**_cart, "cart_id": _cid.strip()}
                _pn = str(_provider_name or "").lower()
                if "swiggy" in _pn:
                    next_session_cart_provider = "swiggy"
                elif "zomato" in _pn:
                    next_session_cart_provider = "zomato"
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
        # Zepto session-state harvests. Executor's diagnostics already
        # carry the merged (seed + fresh) lists, so overwriting wholesale
        # is the right call — same pattern as session_movies / events.
        _zprod = _diag.get("session_zepto_products")
        if isinstance(_zprod, list) and _zprod:
            cleaned_zp: list[dict[str, Any]] = []
            for entry in _zprod:
                if not isinstance(entry, dict):
                    continue
                pvid = str(entry.get("product_variant_id") or "").strip()
                spid = str(entry.get("store_product_id") or "").strip()
                name = str(entry.get("name") or "").strip()
                if pvid and spid and name:
                    cleaned_zp.append(
                        {
                            **entry,
                            "product_variant_id": pvid,
                            "store_product_id": spid,
                            "name": name,
                        }
                    )
            if cleaned_zp:
                next_session_zepto_products = cleaned_zp
        _zaddr = _diag.get("session_zepto_addresses")
        if isinstance(_zaddr, list) and _zaddr:
            cleaned_za: list[dict[str, Any]] = []
            for entry in _zaddr:
                if not isinstance(entry, dict):
                    continue
                aid = str(entry.get("address_id") or "").strip()
                label = str(entry.get("label") or "").strip()
                if aid and label:
                    cleaned_za.append(
                        {**entry, "address_id": aid, "label": label}
                    )
            if cleaned_za:
                next_session_zepto_addresses = cleaned_za
        _zcart = _diag.get("session_zepto_cart")
        if isinstance(_zcart, dict):
            # Always trust executor's cart snapshot when present — even
            # an empty cart (``is_empty: true``) is a legitimate state
            # the frontend should render.
            next_session_zepto_cart = dict(_zcart)

        # Instamart echoes — same shape, different keys.
        _improd = _diag.get("session_instamart_products")
        if isinstance(_improd, list) and _improd:
            cleaned_ip: list[dict[str, Any]] = []
            for entry in _improd:
                if not isinstance(entry, dict):
                    continue
                spin = entry.get("spin_id")
                name = entry.get("name")
                if (
                    isinstance(spin, str)
                    and spin.strip()
                    and isinstance(name, str)
                    and name.strip()
                ):
                    cleaned_ip.append(
                        {**entry, "spin_id": spin.strip(), "name": name.strip()}
                    )
            if cleaned_ip:
                next_session_instamart_products = cleaned_ip
        _imaddr = _diag.get("session_instamart_addresses")
        if isinstance(_imaddr, list) and _imaddr:
            cleaned_ia: list[dict[str, Any]] = []
            for entry in _imaddr:
                if not isinstance(entry, dict):
                    continue
                aid = entry.get("address_id")
                if isinstance(aid, str) and aid.strip():
                    cleaned_ia.append({**entry, "address_id": aid.strip()})
            if cleaned_ia:
                next_session_instamart_addresses = cleaned_ia
        _imcart = _diag.get("session_instamart_cart")
        if isinstance(_imcart, dict):
            next_session_instamart_cart = dict(_imcart)

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
        "uber_execution": uber_execution,
        "observable_trace": build_tool_execution_trace(
            plan=plan if isinstance(plan, dict) else {},
            selected_providers=routed,
            tool_execution=(
                tool_execution
                if execute_tools and task_type not in {"reminder_calendar", "maps_query", "cab_booking"}
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
        # District event-surface indexes — same echo contract as
        # session_movies / session_theatres. Frontend round-trips these so
        # the planner can cite literal event/venue/artist ids on follow-ups
        # without re-running discovery.
        "session_events": next_session_events,
        "session_venues": next_session_venues,
        "session_artists": next_session_artists,
        # Active District table reservation (single dict — District table
        # bookings are 1-per-cart). Replaced wholesale by each successful
        # create_table_reservation_booking; carried unchanged otherwise.
        "session_reservation": next_session_reservation,
        # Per-item LRU memory — every (res_id, variant_id, addon_groups)
        # tuple the user has been shown this session. Lets the next turn's
        # planner emit ``create_cart`` directly when the user picks one,
        # and carries the customization schema across turns so addon picks
        # ("with buttermilk") can be resolved against the already-known
        # leaf options.
        "session_menu": next_session_menu,
        # Current Zomato cart snapshot (single-restaurant by design). Replaced
        # wholesale by the next successful ``create_cart``; carried unchanged
        # on every other turn. Lets the response LLM tell the user what's in
        # the cart without re-asking Zomato or hallucinating from history.
        "session_cart": next_session_cart,
        # Saved-address memory — same echo contract as session_restaurants.
        # Lets the chat work with zero "fetch addresses" clicks on the
        # frontend: turn 1 auto-prefetches Zomato addresses, turns 2+ just
        # round-trip the list back and skip the MCP call.
        "session_addresses": next_session_addresses,
        # Zepto session memory. ``session_zepto_products`` is an LRU of
        # every product (name + pvid + spid + price/pack metadata) the
        # user has seen; the planner pulls literal ids from here when the
        # user picks an item by name. ``session_zepto_addresses`` mirrors
        # ``session_addresses`` but holds Zepto's own saved-address ids.
        # ``session_zepto_cart`` is a single dict snapshot of the active
        # cart, kept in sync with ``view_cart`` and optimistically
        # updated after every ``update_cart`` call.
        "session_zepto_products": next_session_zepto_products,
        "session_zepto_addresses": next_session_zepto_addresses,
        "session_zepto_cart": next_session_zepto_cart,
        # Instamart session memory — same shape as Zepto, but keyed on
        # ``spin_id`` (Instamart's per-variant cart key). Round-tripped so the
        # planner can call ``update_cart`` directly when the user picks an
        # item by name. ``session_instamart_addresses`` mirrors saved-address
        # data; ``session_instamart_cart`` is the latest ``get_cart`` snapshot.
        "session_instamart_products": next_session_instamart_products,
        "session_instamart_addresses": next_session_instamart_addresses,
        "session_instamart_cart": next_session_instamart_cart,
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
    elif task_type == "cab_booking":
        assistant_message = reply_uber_cab(
            user_message=user_message,
            execution=uber_execution,
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
        # LLM-driven cart_card emission for the "no-tool, just describe the
        # cart" path. Auto-emit (fresh ``create_cart`` this turn) is already
        # handled by ``_collect_cart_attachments`` above; this branch only
        # adds a card when:
        #   * the response LLM signalled ``show_cart: true`` (user asked
        #     "what's in my cart", "kitna ho gaya", "ready to checkout?", …),
        #   * we have a real cart to render (``next_session_cart`` carries
        #     the seed forward when no fresh capture happened), AND
        #   * no cart_card was already emitted this turn (avoids duplicates
        #     after a fresh create_cart confirmation).
        show_cart = bool(response_payload.get("show_cart"))
        already_has_cart_card = any(
            isinstance(a, dict) and str(a.get("kind") or "") == "cart_card"
            for a in attachments
        )
        if (
            show_cart
            and not already_has_cart_card
            and isinstance(next_session_cart, dict)
        ):
            # Tag the source/provider off the last carry-forward so a
            # Swiggy ``what's in my cart?`` turn renders the Swiggy-orange
            # cart card (not the Zomato one).
            _src_provider = (
                next_session_cart_provider
                or ("swiggy" if str(out.get("routed_provider") or "").lower() == "swiggy_food"
                    else "zomato")
            )
            extra = _build_cart_card_attachment(
                next_session_cart, source=f"{_src_provider}.session_cart"
            )
            if extra is not None:
                attachments = attachments + [extra]
                out["attachments"] = attachments
                trace(
                    "response_builder.cart_card_emitted",
                    cart_id=str(next_session_cart.get("cart_id") or ""),
                    item_count=len(next_session_cart.get("items") or []),
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
