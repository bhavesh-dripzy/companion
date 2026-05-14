from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


def _preferred_address_id_for_summary(zomato: dict[str, Any]) -> str | None:
    """ID used for restaurant search — prefer executor diagnostics, then restaurant tool args."""
    diag = zomato.get("diagnostics") if isinstance(zomato.get("diagnostics"), dict) else {}
    for key in ("resolved_address_id", "requested_address_id"):
        v = str(diag.get(key) or "").strip()
        if v:
            return v
    for step in zomato.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tool") not in ("get_restaurants_for_keyword", "search_restaurants"):
            continue
        args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        aid = str(args.get("address_id") or "").strip()
        if aid:
            return aid
    return None


def _extract_primary_address(zomato: dict[str, Any] | None) -> str | None:
    if not isinstance(zomato, dict):
        return None

    preferred_id = _preferred_address_id_for_summary(zomato)

    for step in zomato.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tool") not in (
            "get_saved_addresses_for_user",
            "get_saved_addresses_for_user(auto)",
            "get_user_addresses",
            "get_user_addresses(auto)",
            "get_addresses",
            "get_addresses(auto)",
        ):
            continue
        payload = ((step.get("result") or {}).get("data") or {})
        result = payload.get("result") if isinstance(payload, dict) else {}
        addresses = None
        if isinstance(result, dict):
            for key in ("addresses", "user_addresses", "saved_addresses"):
                if isinstance(result.get(key), list):
                    addresses = result[key]
                    break
        if addresses is None and isinstance(payload, dict):
            for key in ("addresses", "user_addresses", "saved_addresses"):
                if isinstance(payload.get(key), list):
                    addresses = payload[key]
                    break
        if not (isinstance(addresses, list) and addresses):
            continue
        if preferred_id:
            for addr in addresses:
                if not isinstance(addr, dict):
                    continue
                if str(addr.get("address_id") or addr.get("addressId") or addr.get("id") or "").strip() != preferred_id:
                    continue
                location_name = (
                    str(addr.get("location_name") or addr.get("name") or addr.get("label") or "").strip()
                )
                if location_name:
                    return location_name
        for addr in addresses:
            if not isinstance(addr, dict):
                continue
            location_name = (
                str(addr.get("location_name") or addr.get("name") or addr.get("label") or "").strip()
            )
            if location_name:
                return location_name
    return None


def _extract_restaurants(zomato: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(zomato, dict):
        return []

    found: list[dict[str, Any]] = []
    for step in zomato.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tool") not in ("get_restaurants_for_keyword", "search_restaurants"):
            continue
        payload = ((step.get("result") or {}).get("data") or {})
        results = payload.get("results") if isinstance(payload, dict) else None
        if results is None and isinstance(payload, dict):
            results = payload.get("restaurants")
        if not isinstance(results, list):
            continue
        for r in results:
            if not isinstance(r, dict):
                continue
            items = r.get("items")
            top_item = None
            if isinstance(items, list) and items and isinstance(items[0], dict):
                top_item = items[0].get("name")
            found.append(
                {
                    "name": str(r.get("name") or "Unknown"),
                    "rating": r.get("rating"),
                    "distance": r.get("distance"),
                    "eta": r.get("eta"),
                    "offer": r.get("res_offer"),
                    "top_item": top_item,
                }
            )
    return found


def _menu_item_price(obj: dict[str, Any]) -> Any:
    for key in (
        "discounted_price",
        "price",
        "display_price",
        "item_price",
        "mrp",
        "sp",
        "cost",
        "amount",
    ):
        if obj.get(key) is not None:
            return obj.get(key)
    return None


def _has_menu_leaf_id(obj: dict[str, Any]) -> bool:
    for key in (
        "item_id",
        "dish_id",
        "menu_item_id",
        "product_id",
        "variant_id",
        "res_item_id",
        "catalogue_id",
        "catalog_id",
    ):
        v = obj.get(key)
        if v is None or v == "" or v == 0:
            continue
        return True
    return False


def _is_menu_category_bucket(obj: dict[str, Any]) -> bool:
    """Skip section headers (e.g. category dict with nested items but no dish row ids)."""
    if _has_menu_leaf_id(obj) or _menu_item_price(obj) is not None:
        return False
    for key in ("items", "dishes", "menu_items", "products"):
        v = obj.get(key)
        if isinstance(v, list) and len(v) > 0:
            return True
    return False


def _menu_item_display_name(obj: dict[str, Any]) -> str | None:
    for key in (
        "name",
        "item_name",
        "title",
        "dish_name",
        "product_name",
        "display_name",
        "label",
        "item_title",
        "variant_name",
        "product_title",
        "menu_item_name",
        "nm",
    ):
        v = obj.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if 0 < len(s) <= 200:
            return s
    for key in ("text", "description"):
        v = obj.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if 8 < len(s) <= 120:
            return s
    return None


def _normalize_menu_step_payload(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw_text": raw}
    return raw


def _extract_item_mappings_from_listing_payload(payload: Any) -> list[dict[str, Any]] | None:
    """Zomato get_menu_items_listing returns result.data.item_mappings."""
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    ims = data.get("item_mappings")
    if isinstance(ims, list) and ims:
        return [x for x in ims if isinstance(x, dict)]
    return None


def _extract_menu_items(zomato: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(zomato, dict):
        return []

    found: list[dict[str, Any]] = []
    for step in zomato.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tool") not in (
            "get_restaurant_menu_by_categories",
            "get_menu_items_listing",
            "get_restaurant_menu",
            "search_menu",
            "search_menu_items",
            "search_products",
            "get_product_details",
        ):
            continue
        payload = _normalize_menu_step_payload(((step.get("result") or {}).get("data") or {}))
        if step.get("tool") == "get_menu_items_listing":
            ims = _extract_item_mappings_from_listing_payload(payload)
            if ims:
                for it in ims:
                    name = str(it.get("item_name") or "").strip() or _menu_item_display_name(it)
                    if not name:
                        continue
                    found.append({"name": name, "price": _menu_item_price(it)})
                    if len(found) >= 12:
                        return found
                continue
        for obj in _walk_payload(payload):
            if not isinstance(obj, dict):
                continue
            if _is_menu_category_bucket(obj):
                continue
            name = _menu_item_display_name(obj)
            if not name:
                continue
            price = _menu_item_price(obj)
            found.append({"name": name, "price": price})
            if len(found) >= 12:
                return found
    return found


def _walk_payload(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_payload(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_payload(value)


def build_zomato_summary(zomato: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(zomato, dict):
        return {
            "ok": False,
            "error": "zomato_not_executed",
            "address": None,
            "top_restaurants": [],
            "menu_items": [],
            "count": 0,
            "diagnostics": {},
        }

    restaurants = _extract_restaurants(zomato)
    menu_items = _extract_menu_items(zomato)
    summary: dict[str, Any] = {
        "ok": bool(zomato.get("ok")),
        "error": None if zomato.get("ok") else str(zomato.get("error_detail") or "unknown_error"),
        "address": _extract_primary_address(zomato),
        "top_restaurants": restaurants[:8],
        "menu_items": menu_items[:8],
        "count": len(restaurants),
        "diagnostics": zomato.get("diagnostics") if isinstance(zomato.get("diagnostics"), dict) else {},
    }
    return summary


def _fallback_assistant_message(
    *,
    plan: dict[str, Any],
    zomato_summary: dict[str, Any],
) -> str:
    summary = str((plan or {}).get("summary") or "").strip()
    if not zomato_summary.get("ok"):
        detail = str(zomato_summary.get("error") or "Unknown tool execution error.")
        if detail == "zomato_not_executed":
            return (
                "I understood your request"
                + (f": {summary}." if summary else ".")
                + " Zomato MCP was not executed for this request. "
                + "Connect MCP, run Test, then retry the search."
            )
        if detail == "zomato_requires_saved_address_for_requested_location":
            requested = (
                (zomato_summary.get("diagnostics") or {}).get("requested_location")
                if isinstance(zomato_summary.get("diagnostics"), dict)
                else None
            )
            place = f" for {requested}" if requested else ""
            return (
                "I understood your request"
                + (f": {summary}." if summary else ".")
                + f" Zomato MCP needs a saved delivery address{place} before it can "
                "return serviceable restaurants. I did not use your default saved address "
                "because that would show the wrong locality."
            )
        if detail == "tool_plan_incomplete":
            return (
                "I found part of what was needed"
                + (f": {summary}." if summary else ".")
                + " The next Zomato tool needs an ID that was not present in the earlier result. "
                "Pick a restaurant or item from the results and I can continue."
            )
        return (
            "I understood your request"
            + (f": {summary}." if summary else ".")
            + f" But I hit an issue while talking to Zomato tools: {detail}. "
            + "Would you like me to retry or refine your request?"
        )

    restaurants = zomato_summary.get("top_restaurants") or []
    menu_items = zomato_summary.get("menu_items") or []
    if menu_items:
        names = ", ".join(str(r.get("name") or "Unknown") for r in menu_items[:5])
        return (
            "I found matching menu items"
            + (f": {summary}." if summary else ".")
            + f" Options include: {names}. "
            + "Which one should I inspect or add next?"
        )

    if not restaurants:
        return (
            "I understood your request"
            + (f": {summary}." if summary else ".")
            + " I could not find strong matches yet. "
            + "Would you like me to refine by cuisine, budget, veg preference, or rating?"
        )

    top = restaurants[:3]
    names = ", ".join(str(r.get("name") or "Unknown") for r in top)
    return (
        "Here are good options"
        + (f": {summary}." if summary else ".")
        + f" Top picks: {names}. "
        + "What should I do next? You can ask for menu, filters, or add an item to cart."
    )


def _openai_compatible_chat(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    base_url: str | None = None,
) -> str:
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    completion = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (completion.choices[0].message.content or "").strip()


def build_assistant_message_with_llm(
    *,
    user_message: str,
    plan: dict[str, Any],
    zomato_summary: dict[str, Any],
    provider: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
) -> str:
    system_prompt = (
        "You are a food ordering assistant. Convert structured tool output into a user-facing response.\n"
        "Rules:\n"
        "1) Be concise and clear.\n"
        "2) Mention top options only if present.\n"
        "3) Never expose internal tool/debug data.\n"
        "4) If zomato_summary.menu_items is a non-empty list, list several dish names (and prices when present) "
        "from that array — do not claim the menu is unavailable.\n"
        "5) Use zomato_summary.address for locality when describing where results apply.\n"
        "6) End with one clear next-step question.\n"
        "7) Include 2-4 concrete suggestions the user can reply with.\n"
    )

    payload = {
        "user_message": user_message,
        "routing_plan": {
            "intent": plan.get("intent"),
            "summary": plan.get("summary"),
            "constraints": plan.get("constraints"),
        },
        "zomato_summary": zomato_summary,
    }

    user_prompt = (
        "Using this JSON, write the assistant reply to the user:\n"
        + json.dumps(payload, ensure_ascii=True)
    )

    try:
        if provider in ("openai", "deepseek"):
            text = _openai_compatible_chat(
                api_key=api_key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                base_url=base_url if provider == "deepseek" else None,
            )
            if text:
                return text
            return _fallback_assistant_message(plan=plan, zomato_summary=zomato_summary)

        from google import genai as google_genai

        client = google_genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}",
        )
        text = (response.text or "").strip()
        if text:
            return text
        return _fallback_assistant_message(plan=plan, zomato_summary=zomato_summary)
    except Exception:
        return _fallback_assistant_message(plan=plan, zomato_summary=zomato_summary)
