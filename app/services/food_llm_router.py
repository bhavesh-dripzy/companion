"""
Maps free-text food requests to a JSON plan typed for downstream MCP tool calls.

Downstream can resolve placeholders such as __ADDRESS_ID__ and __RESTAURANT_ID__
from earlier tool results in the same plan.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from openai import OpenAI

logger = logging.getLogger(__name__)

Provider = Literal["openai", "gemini", "deepseek"]

_ALLOWED_TOOLS = frozenset(
    {
        "get_saved_addresses_for_user",
        "get_restaurants_for_keyword",
        "get_restaurant_menu_by_categories",
        "get_menu_items_listing",
        "create_cart",
        "get_cart_offers",
        "checkout_cart",
        "get_order_history",
        "get_order_tracking_info",
        "bind_user_number",
        "bind_user_number_verify_code",
        "search_restaurants",
        "get_restaurant_menu",
        "search_menu",
        "search_menu_items",
        "get_addresses",
        "get_user_addresses",
        "get_cart",
        "get_food_cart",
        "update_food_cart",
        "flush_food_cart",
        "add_item_to_cart",
        "update_cart_item",
        "remove_cart_item",
        "clear_cart",
        "search_products",
        "get_product_details",
        "place_order",
        "place_food_order",
        "checkout",
        "get_order_details",
        "get_food_orders",
        "get_food_order_details",
        "get_orders",
        "track_food_order",
        "track_order",
        "fetch_food_coupons",
        "apply_food_coupon",
        "get_cart_offers",
        "your_go_to_items",
        "update_cart",
        "report_error",
    }
)

_ADDRESS_TOOLS = {"get_restaurants_for_keyword", "search_restaurants"}
_RESTAURANT_TOOLS = {
    "get_restaurant_menu_by_categories",
    "get_menu_items_listing",
    "get_restaurant_menu",
    "search_menu",
    "search_menu_items",
}
_ORDER_TOOLS = {
    "get_order_tracking_info",
    "get_order_details",
    "get_food_order_details",
    "track_food_order",
    "track_order",
}
_ORDER_HISTORY_PROVIDERS = {"zomato", "swiggy_food", "swiggy_instamart", "zepto"}

_SYSTEM_PROMPT = """\
You translate the user's message into a machine-ready plan for available MCP tools.
Today the available provider is Zomato MCP.

Respond with ONLY a single JSON object (no markdown fences) shaped exactly like this:
{
  "intent": "<short snake_case intent label>",
  "summary": "<one sentence what the user wants>",
  "tool_plan": [
    {
      "tool": "<exact tool name>",
      "arguments": { }
    }
  ],
    "constraints": {
        "requires_address_resolution": true | false,
        "requested_location_text": "<explicit area/locality/city if user named one, else null>",
        "inferred_keyword": "<string|null>",
    "price_max_inr": <number|null>,
    "price_min_inr": <number|null>,
    "min_rating": <number|null>,
    "near_and_fast": true | false | null,
    "vegetarian_only": true | false | null,
    "party_size_estimate": <integer|null>,
    "health_constraints": "<string|null>"
  },
  "execution_notes_for_downstream": "<optional hints>"
}

TOOLS (use ONLY these exact tool strings):
- get_saved_addresses_for_user: returns the user's saved delivery addresses and address_id values.
- get_restaurants_for_keyword: accepts keyword and address_id. Use "__ADDRESS_ID__" when it should come from saved addresses.
- get_menu_items_listing: PRIMARY for "show menu", items, or prices at a restaurant. After search, call with {"res_id": "__RESTAURANT_ID__", "address_id": "__ADDRESS_ID__"} — Zomato requires both (res_id comes from get_restaurants_for_keyword results).
- get_restaurant_menu_by_categories: optional; requires res_id, address_id, AND exact menu category names from Zomato. Prefer get_menu_items_listing unless the user named specific categories you can supply.
- create_cart: use only when the user has chosen a concrete restaurant and item/variant from earlier results or supplied exact IDs.
- get_cart_offers: use only after a cart exists.
- checkout_cart: use only when the user explicitly confirms checkout/payment.
- get_order_history: use for past orders.
- get_order_tracking_info: use only when the user gives an order id or after order history can provide one.
- bind_user_number: use only when the user asks to bind a phone number.
- bind_user_number_verify_code: use only when the user provides the verification code.

PLACEHOLDERS:
- "__ADDRESS_ID__" may be resolved from get_saved_addresses_for_user.
- "__RESTAURANT_ID__" may be resolved from get_restaurants_for_keyword.
- "__MENU_ITEM_ID__", "__ITEM_ID__", "__VARIANT_ID__", "__CART_ID__", and "__ORDER_ID__" may be resolved only from earlier matching tool output.

RULES:
1. Typical flow for restaurant discovery:
   get_saved_addresses_for_user
   THEN
   get_restaurants_for_keyword with {"keyword": "<cuisine/dish>", "address_id": "__ADDRESS_ID__"}

2. If user asks for food near them, near home, nearby, or without a named city/locality:
   ALWAYS include BOTH tools.

3. If user names a far-off city/locality such as "Saket Delhi" or "MG Road Bengaluru",
   still set constraints.requested_location_text, but do not pretend Zomato can geocode it.
   Zomato restaurant discovery needs a saved delivery address_id. The app will ask the user
   to save/select that delivery address instead of using their default address.

4. For menu / "show menu" / items / prices without a known res_id, plan:
   get_saved_addresses_for_user
   get_restaurants_for_keyword (keyword = restaurant name or dish from the user)
   get_menu_items_listing with {"res_id": "__RESTAURANT_ID__", "address_id": "__ADDRESS_ID__"}

5. Use get_restaurant_menu_by_categories only when the user asks for named menu sections AND you can fill "categories" with valid Zomato category strings (often learned from item listings). Always include res_id and address_id placeholders as in rule 4.

6. Never invent address IDs, restaurant IDs, menu item IDs, variant IDs, cart IDs, or order IDs.

7. Prefer useful multi-step plans over a single tool when later tools can consume IDs from earlier tools.

8. Return ONLY valid JSON.
"""

_SWIGGY_FOOD_SYSTEM_PROMPT = """\
You translate the user's message into a machine-ready plan for Swiggy Food MCP.

Respond with ONLY a single JSON object shaped exactly like the Zomato router schema:
intent, summary, tool_plan, constraints, execution_notes_for_downstream.

TOOLS (use ONLY these exact tool strings):
- search_restaurants: find restaurants for a cuisine/dish/local query.
- get_restaurant_menu: fetch menu for a known restaurant id. Use "__RESTAURANT_ID__" after search_restaurants.
- search_menu: search menu items inside a known restaurant. Use "__RESTAURANT_ID__" after search_restaurants.
- get_addresses: list saved delivery addresses when needed.
- get_food_cart, update_food_cart, flush_food_cart, fetch_food_coupons, apply_food_coupon.
- place_food_order only after explicit checkout confirmation.
- get_food_orders, get_food_order_details, track_food_order.

RULES:
1. For discovery, use search_restaurants with a SHORT keyword or restaurant name only (e.g. "Galaxy Pizza", "biryani") — never put the full summary or "User wants…" prose in "query".
2. For menu questions without a restaurant id, use search_restaurants then get_restaurant_menu with "__RESTAURANT_ID__" and "__ADDRESS_ID__" (include get_restaurant_menu in tool_plan, not only in intent).
3. For ordering/cart changes, do not invent item, variant, restaurant, cart, or address IDs.
4. If the user asks for last/recent/past orders or order history, ALWAYS use get_food_orders.
5. Prefer multi-step plans when a later tool can consume IDs from earlier output.
6. tool_plan must NEVER be an empty array. If you are unsure, output get_addresses then search_restaurants with a dish/cuisine query from the user (then get_restaurant_menu or search_menu only after you have placeholders for restaurant and address).
7. Return ONLY valid JSON.
"""

_SWIGGY_INSTAMART_SYSTEM_PROMPT = """\
You translate the user's message into a machine-ready plan for Swiggy Instamart MCP.

Respond with ONLY a single JSON object shaped exactly like the food router schema:
intent, summary, tool_plan, constraints, execution_notes_for_downstream.

TOOLS (use ONLY these exact tool strings):
- search_products: search grocery/quick-commerce products.
- your_go_to_items: repeat or habitual SKUs when relevant.
- get_addresses: list saved delivery addresses when needed.
- get_cart, update_cart, clear_cart, checkout.
- get_orders, track_order.

RULES:
1. For grocery discovery, use search_products with the user's product terms.
2. For product detail questions, use search_products then get_product_details with "__ITEM_ID__" if that tool is available in your tool list; otherwise rely on search_products cards only.
3. For cart/order flows, do not invent product, variant, cart, address, or order IDs.
4. If the user asks for last/recent/past orders or order history, ALWAYS use get_orders.
5. Return ONLY valid JSON.
6. tool_plan must NEVER be an empty array. If unsure, output get_addresses then search_products with the user's product terms.
"""

_ZEPTO_SYSTEM_PROMPT = """\
You translate the user's message into a machine-ready plan for the official Zepto MCP.

Respond with ONLY a single JSON object shaped exactly like the food router schema:
intent, summary, tool_plan, constraints, execution_notes_for_downstream.

TOOLS (use ONLY these exact tool strings):
- get_user_preferences: retrieve the user's Zepto brand/category preferences before broad shopping searches when useful.
- search_products: search Zepto's live product catalog.
- get_product_details: fetch details for a known product id. Use "__ITEM_ID__" only after search_products.
- get_cart, add_item_to_cart, update_cart_item, remove_cart_item, clear_cart, get_cart_offers.
- checkout or place_order only after explicit user confirmation. Mention payment method only if the user asked for one.
- get_order_history, get_order_details.

RULES:
1. For grocery discovery, use get_user_preferences first when the request is broad, then search_products.
2. For quick shopping lists, use one search_products step per item family before cart changes.
3. For product detail questions, use search_products then get_product_details with "__ITEM_ID__".
4. Never invent product, variant, cart, address, or order IDs.
5. Zepto orders are real orders. Do not checkout/place_order without explicit confirmation.
6. If the user asks for last/recent/past orders or order history, ALWAYS use get_order_history.
7. Return ONLY valid JSON.
"""


def _system_prompt_for_tool_provider(
    tool_provider: str | None,
    *,
    tool_provider_label: str | None = None,
    tool_provider_tools: list[str] | None = None,
) -> str:
    if tool_provider_tools:
        tools = "\n".join(f"- {tool}" for tool in tool_provider_tools)
        label = tool_provider_label or tool_provider or "Custom MCP"
        return f"""\
You translate the user's message into a machine-ready plan for {label}.

Respond with ONLY a single JSON object shaped exactly like this:
{{
  "intent": "<short snake_case intent label>",
  "summary": "<one sentence what the user wants>",
  "tool_plan": [
    {{"tool": "<exact tool name>", "arguments": {{}}}}
  ],
  "constraints": {{"requires_address_resolution": false, "requested_location_text": null, "inferred_keyword": null}},
  "execution_notes_for_downstream": "<optional hints>"
}}

TOOLS (use ONLY these exact tool strings):
{tools}

RULES:
1. Use the smallest useful chain of tools.
2. Never invent opaque IDs. If a later tool needs an ID, use a placeholder like "__ITEM_ID__", "__ORDER_ID__", "__CART_ID__", or "__RESTAURANT_ID__" only after an earlier tool can return it.
3. Do not call destructive or purchasing tools unless the user explicitly confirms that action.
4. Return ONLY valid JSON.
"""
    if tool_provider == "swiggy_food":
        return _SWIGGY_FOOD_SYSTEM_PROMPT
    if tool_provider == "swiggy_instamart":
        return _SWIGGY_INSTAMART_SYSTEM_PROMPT
    if tool_provider == "zepto":
        return _ZEPTO_SYSTEM_PROMPT
    return _SYSTEM_PROMPT


def _infer_keyword_from_message(message: str) -> str:
    text = " ".join(message.strip().split())
    if not text:
        return "restaurant"
    lowered = text.lower()
    for marker in ("compare", "find", "search", "show", "get", "order"):
        if lowered.startswith(marker + " "):
            text = text[len(marker) :].strip()
            lowered = text.lower()
            break
    for sep in (" near ", " in ", " at ", " around ", " from "):
        if sep in lowered:
            idx = lowered.index(sep)
            text = text[:idx].strip()
            break
    for word in ("zomato", "swiggy", "blinkit", "zepto", "versus", "vs"):
        text = " ".join(part for part in text.split() if part.lower().strip(".,") != word)
    return text.strip(" ,.-") or "restaurant"


def _comparison_plan(user_message: str, tool_providers: set[str]) -> dict[str, Any] | None:
    wanted = {"zomato", "swiggy_food"}
    text = user_message.lower()
    asks_compare = any(token in text for token in ("compare", " vs ", " versus "))
    mentions_both = "zomato" in text and "swiggy" in text
    if not (wanted <= tool_providers or (asks_compare and mentions_both)):
        return None

    keyword = _infer_keyword_from_message(user_message)
    plan = [
        {"provider": "zomato", "tool": "get_saved_addresses_for_user", "arguments": {}},
        {
            "provider": "zomato",
            "tool": "get_restaurants_for_keyword",
            "arguments": {"keyword": keyword, "address_id": "__ADDRESS_ID__"},
        },
        {"provider": "swiggy_food", "tool": "search_restaurants", "arguments": {"query": keyword}},
    ]
    return {
        "intent": "compare_restaurant_providers",
        "summary": f"Compare Zomato and Swiggy results for {keyword}.",
        "tool_plan": plan,
        "constraints": {
            "requires_address_resolution": True,
            "requested_location_text": None,
            "inferred_keyword": keyword,
            "price_max_inr": None,
            "price_min_inr": None,
            "min_rating": None,
            "near_and_fast": None,
            "vegetarian_only": None,
            "party_size_estimate": None,
            "health_constraints": None,
            "comparison_providers": sorted(wanted),
        },
        "execution_notes_for_downstream": (
            "Run Zomato and Swiggy Food independently, then compare availability, rating, ETA, distance, and offers."
        ),
        "validation": {
            "tool_count": len(plan),
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "deterministic-comparison-router",
            "completion_id": "",
            "provider": "system",
        },
    }


def _order_history_plan(user_message: str, tool_providers: set[str]) -> dict[str, Any] | None:
    text = f" {user_message.lower()} "
    order_terms = (" order ", " orders ", " ordered ", " history ", " recent ", " last ", " past ")
    if not any(term in text for term in order_terms):
        return None

    providers = sorted(tool_providers & _ORDER_HISTORY_PROVIDERS)
    if not providers:
        return None

    plan = []
    for provider_name in providers:
        if provider_name == "swiggy_food":
            plan.append({"provider": provider_name, "tool": "get_food_orders", "arguments": {}})
        elif provider_name == "swiggy_instamart":
            plan.append({"provider": provider_name, "tool": "get_orders", "arguments": {}})
        else:
            plan.append({"provider": provider_name, "tool": "get_order_history", "arguments": {}})
    return {
        "intent": "get_order_history",
        "summary": "Fetch recent order history from " + ", ".join(providers) + ".",
        "tool_plan": plan,
        "constraints": {
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
            "order_history_providers": providers,
        },
        "execution_notes_for_downstream": "Run order-history tools for the selected provider(s) (get_food_orders / get_orders / get_order_history).",
        "validation": {
            "tool_count": len(plan),
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "deterministic-order-history-router",
            "completion_id": "",
            "provider": "system",
        },
    }


def _has_tool(tool_plan: list[dict[str, Any]], tool_name: str) -> bool:
    return any(isinstance(step, dict) and step.get("tool") == tool_name for step in tool_plan)


def _constraint_text(constraints: dict[str, Any], key: str) -> str | None:
    val = constraints.get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _default_keyword(data: dict[str, Any], constraints: dict[str, Any]) -> str:
    keyword = _constraint_text(constraints, "inferred_keyword")
    if keyword:
        return keyword
    summary = str(data.get("summary") or "").strip()
    return summary or "restaurant"


def _discovery_keyword_from_constraints(
    data: dict[str, Any], constraints: dict[str, Any]
) -> str:
    for key in ("query", "inferred_keyword", "keyword"):
        v = _constraint_text(constraints, key)
        if v:
            return v
    return _default_keyword(data, constraints)


def _fallback_tool_plan_swiggy_food(
    data: dict[str, Any], constraints: dict[str, Any]
) -> list[dict[str, Any]]:
    """When the LLM returns [], still run a minimal Swiggy discovery chain."""
    q = _discovery_keyword_from_constraints(data, constraints)
    return [
        {"tool": "get_addresses", "arguments": {}},
        {"tool": "search_restaurants", "arguments": {"query": q}},
    ]


def _fallback_tool_plan_swiggy_instamart(
    data: dict[str, Any], constraints: dict[str, Any]
) -> list[dict[str, Any]]:
    q = _discovery_keyword_from_constraints(data, constraints)
    if not q or q.strip().lower() == "restaurant":
        q = "groceries"
    return [
        {"tool": "get_addresses", "arguments": {}},
        {"tool": "search_products", "arguments": {"query": q}},
    ]


def _swiggy_search_query_from_context(
    raw_query: str,
    *,
    constraints: dict[str, Any],
    data: dict[str, Any],
) -> str:
    """Turn bloated LLM prose into a short Swiggy search_restaurants query."""
    q = (raw_query or "").strip()
    name = _constraint_text(constraints, "restaurant_name")
    if name and (len(q) > 64 or re.match(r"(?is)^(user wants|i want|i'd like|show me|please)\b", q)):
        return name.strip()
    inf = _constraint_text(constraints, "inferred_keyword")
    if inf and len(q) > 64:
        return inf.strip()
    summary = str(data.get("summary") or "").strip()
    low = summary.lower()
    if " for " in low:
        m = re.search(r"for\s+([^.\n]+?)(?:\.|$)", summary, re.I)
        if m:
            frag = m.group(1).strip().strip("'\"")
            if 2 <= len(frag) <= 80:
                return frag
    if len(q) > 88:
        return (name or inf or _default_keyword(data, constraints) or q[:72]).strip()
    return q or "restaurant"


def _sanitize_swiggy_food_search_queries(
    tool_plan: list[dict[str, Any]],
    data: dict[str, Any],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "search_restaurants":
            out.append(step)
            continue
        args = dict(step.get("arguments") or {})
        q = str(args.get("query") or "").strip()
        args["query"] = _swiggy_search_query_from_context(q, constraints=constraints, data=data)
        out.append({**step, "arguments": args})
    return out


def _wants_swiggy_menu_fetch(
    intent: str, summary: str, constraints: dict[str, Any]
) -> bool:
    intent_l = (intent or "").lower()
    if "menu" in intent_l or intent_l in ("get_restaurant_menu", "search_menu", "search_menu_items"):
        return True
    if _constraint_text(constraints, "menu_type"):
        return True
    s = (summary or "").lower()
    if "menu" in s or "veg " in s or " items" in s or "dishes" in s:
        return True
    return False


def _maybe_append_swiggy_menu_after_search(
    tool_plan: list[dict[str, Any]],
    data: dict[str, Any],
    constraints: dict[str, Any],
    tool_provider: str | None,
) -> list[dict[str, Any]]:
    """If the model stopped after search but asked for a menu, fetch get_restaurant_menu."""
    if tool_provider != "swiggy_food" or not tool_plan:
        return tool_plan
    if not _wants_swiggy_menu_fetch(
        str(data.get("intent") or ""),
        str(data.get("summary") or ""),
        constraints,
    ):
        return tool_plan
    names = {s.get("tool") for s in tool_plan if isinstance(s, dict)}
    if "get_restaurant_menu" in names or "search_menu" in names:
        return tool_plan
    if "search_restaurants" not in names:
        return tool_plan
    out = list(tool_plan)
    out.append(
        {
            "tool": "get_restaurant_menu",
            "arguments": {
                "restaurantId": "__RESTAURANT_ID__",
                "addressId": "__ADDRESS_ID__",
                "restaurant_id": "__RESTAURANT_ID__",
                "address_id": "__ADDRESS_ID__",
            },
        }
    )
    return out


def _ensure_prerequisites(
    tool_plan: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    constraints: dict[str, Any],
    tool_provider: str | None = None,
) -> list[dict[str, Any]]:
    if not tool_plan:
        return tool_plan

    needs_address = any(step.get("tool") in _ADDRESS_TOOLS for step in tool_plan)
    needs_restaurant = any(step.get("tool") in _RESTAURANT_TOOLS for step in tool_plan)

    address_list_tool = (
        "get_addresses"
        if tool_provider in ("swiggy_food", "swiggy_instamart")
        else "get_saved_addresses_for_user"
    )

    out = list(tool_plan)
    has_addr_prefetch = (
        _has_tool(out, "get_saved_addresses_for_user")
        or _has_tool(out, "get_user_addresses")
        or _has_tool(out, "get_addresses")
    )
    if (
        (needs_address or (needs_restaurant and tool_provider != "swiggy_food"))
        and not _has_tool(out, address_list_tool)
        and not has_addr_prefetch
    ):
        out.insert(0, {"tool": address_list_tool, "arguments": {}})

    if needs_restaurant and not _has_tool(out, "get_restaurants_for_keyword") and not _has_tool(out, "search_restaurants"):
        has_address_header = (
            _has_tool(out, "get_saved_addresses_for_user")
            or _has_tool(out, "get_user_addresses")
            or _has_tool(out, "get_addresses")
        )
        insert_at = 1 if has_address_header else 0
        if tool_provider == "swiggy_food":
            out.insert(
                0,
                {
                    "tool": "search_restaurants",
                    "arguments": {"query": _default_keyword(data, constraints)},
                },
            )
        else:
            out.insert(
                insert_at,
                {
                    "tool": "get_restaurants_for_keyword",
                    "arguments": {
                        "keyword": _default_keyword(data, constraints),
                        "address_id": "__ADDRESS_ID__",
                    },
                },
            )

    return out


def _normalize_zomato_res_id_address_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Zomato MCP menu tools expect res_id + address_id (not restaurant_id alone)."""
    arguments = dict(arguments)
    res_id = (
        arguments.get("res_id")
        or arguments.get("restaurant_id")
        or arguments.get("restaurantId")
        or arguments.get("resId")
    )
    if res_id is None or (isinstance(res_id, str) and not str(res_id).strip()):
        arguments["res_id"] = "__RESTAURANT_ID__"
    else:
        arguments["res_id"] = res_id
    for drop in ("restaurant_id", "restaurantId", "resId"):
        arguments.pop(drop, None)
    aid = arguments.get("address_id")
    if aid is None or (isinstance(aid, str) and not str(aid).strip()):
        arguments["address_id"] = "__ADDRESS_ID__"
    return arguments


def normalize_tool_plan(
    tool_plan: list[dict[str, Any]],
    *,
    allowed_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for step in tool_plan:
        if not isinstance(step, dict):
            continue

        tool = step.get("tool")
        arguments = step.get("arguments") or {}

        if not isinstance(tool, str):
            continue

        if tool == "get_user_addresses":
            tool = "get_addresses"
        elif tool == "search_menu_items":
            tool = "search_menu"

        if tool not in (allowed_tools or _ALLOWED_TOOLS):
            continue

        if not isinstance(arguments, dict):
            arguments = {}

        if tool == "get_restaurants_for_keyword":
            arguments = dict(arguments)
            aid = arguments.get("address_id")
            if aid is None or (isinstance(aid, str) and not str(aid).strip()):
                arguments["address_id"] = "__ADDRESS_ID__"
            keyword = arguments.get("keyword")
            if keyword is None or (isinstance(keyword, str) and not keyword.strip()):
                arguments["keyword"] = "restaurant"

        if tool == "search_restaurants":
            arguments = dict(arguments)
            aid = arguments.get("addressId") or arguments.get("address_id")
            if aid is None or (isinstance(aid, str) and not str(aid).strip()):
                arguments["address_id"] = "__ADDRESS_ID__"
                arguments["addressId"] = "__ADDRESS_ID__"
            else:
                s = str(aid).strip()
                arguments["address_id"] = s
                arguments["addressId"] = s
            q = arguments.get("query") or arguments.get("keyword")
            if q is None or (isinstance(q, str) and not str(q).strip()):
                arguments["query"] = "restaurant"
            else:
                arguments["query"] = str(q).strip()
            arguments.pop("keyword", None)

        if tool in _RESTAURANT_TOOLS:
            arguments = dict(arguments)
            if tool in ("get_menu_items_listing", "get_restaurant_menu_by_categories"):
                arguments = _normalize_zomato_res_id_address_args(arguments)
            elif tool in ("get_restaurant_menu", "search_menu"):
                restaurant_id = (
                    arguments.get("restaurant_id")
                    or arguments.get("restaurantId")
                    or arguments.get("res_id")
                    or arguments.get("resId")
                )
                if restaurant_id is None or (
                    isinstance(restaurant_id, str) and not restaurant_id.strip()
                ):
                    arguments["restaurant_id"] = "__RESTAURANT_ID__"
                    arguments["restaurantId"] = "__RESTAURANT_ID__"
                else:
                    s = str(restaurant_id).strip()
                    arguments["restaurant_id"] = s
                    arguments["restaurantId"] = s
                aid = arguments.get("address_id") or arguments.get("addressId")
                if aid is None or (isinstance(aid, str) and not str(aid).strip()):
                    arguments["address_id"] = "__ADDRESS_ID__"
                    arguments["addressId"] = "__ADDRESS_ID__"
                else:
                    s = str(aid).strip()
                    arguments["address_id"] = s
                    arguments["addressId"] = s
            else:
                restaurant_id = (
                    arguments.get("restaurant_id")
                    or arguments.get("res_id")
                    or arguments.get("restaurantId")
                    or arguments.get("resId")
                )
                if restaurant_id is None or (
                    isinstance(restaurant_id, str) and not restaurant_id.strip()
                ):
                    arguments["restaurant_id"] = "__RESTAURANT_ID__"

        if tool in _ORDER_TOOLS:
            arguments = dict(arguments)
            order_id = arguments.get("order_id") or arguments.get("orderId")
            if order_id is None or (isinstance(order_id, str) and not order_id.strip()):
                arguments["order_id"] = "__ORDER_ID__"

        normalized_step = {
            "tool": tool.strip(),
            "arguments": arguments,
        }
        provider_hint = step.get("provider")
        if isinstance(provider_hint, str) and provider_hint.strip():
            normalized_step["provider"] = provider_hint.strip()
        out.append(normalized_step)

    return out


def _finalize_from_parsed_json(
    data: dict[str, Any],
    *,
    provider: Provider,
    model: str,
    trace_id: str,
    tool_provider: str | None = None,
    allowed_tools: set[str] | None = None,
) -> dict[str, Any]:
    tool_plan_raw = data.get("tool_plan")

    tool_plan = normalize_tool_plan(
        tool_plan_raw if isinstance(tool_plan_raw, list) else [],
        allowed_tools=allowed_tools,
    )

    constraints = data.get("constraints")

    if constraints is None or not isinstance(constraints, dict):
        constraints = {}
    constraints.setdefault("requested_location_text", None)
    if tool_provider == "swiggy_food":
        tool_plan = _sanitize_swiggy_food_search_queries(tool_plan, data, constraints)
    tool_plan = _ensure_prerequisites(
        tool_plan,
        data=data,
        constraints=constraints,
        tool_provider=tool_provider,
    )
    if not tool_plan and tool_provider == "swiggy_food":
        tool_plan = _fallback_tool_plan_swiggy_food(data, constraints)
        tool_plan = _ensure_prerequisites(
            tool_plan,
            data=data,
            constraints=constraints,
            tool_provider=tool_provider,
        )
    elif not tool_plan and tool_provider == "swiggy_instamart":
        tool_plan = _fallback_tool_plan_swiggy_instamart(data, constraints)
        tool_plan = _ensure_prerequisites(
            tool_plan,
            data=data,
            constraints=constraints,
            tool_provider=tool_provider,
        )
    if tool_provider == "swiggy_food":
        tool_plan = _maybe_append_swiggy_menu_after_search(
            tool_plan, data, constraints, tool_provider
        )
        tool_plan = normalize_tool_plan(tool_plan, allowed_tools=allowed_tools)
    constraints.setdefault(
        "requires_address_resolution",
        any(step.get("tool") in _ADDRESS_TOOLS for step in tool_plan),
    )

    normalized: dict[str, Any] = {
        "intent": str(data.get("intent", "") or "unknown"),
        "summary": str(data.get("summary", "") or ""),
        "tool_plan": tool_plan,
        "constraints": constraints,
        "execution_notes_for_downstream": str(
            data.get("execution_notes_for_downstream", "") or ""
        ),
    }

    raw_len = (
        sum(1 for s in tool_plan_raw if isinstance(s, dict))
        if isinstance(tool_plan_raw, list)
        else 0
    )

    normalized["validation"] = {
        "tool_count": len(tool_plan),
        "dropped_unknown_tool_steps": max(0, raw_len - len(tool_plan)),
        "resolved_model": model,
        "completion_id": trace_id,
        "provider": provider,
    }

    return normalized


def _json_from_llm(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("food_llm_router: invalid JSON from model: %s", e)
        raise ValueError("Model returned invalid JSON.") from e

    if not isinstance(data, dict):
        raise ValueError("Model payload must be a JSON object.")

    return data


def _openai_compatible_call(
    *,
    api_key: str,
    model: str,
    user_message: str,
    base_url: str | None = None,
    system_prompt: str = _SYSTEM_PROMPT,
) -> tuple[str, str | None]:
    """Shared path for OpenAI and DeepSeek (both use OpenAI SDK)."""
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    completion = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message.strip()},
        ],
        response_format={"type": "json_object"},
    )

    content = completion.choices[0].message.content or "{}"
    tid = getattr(completion, "id", None) or ""
    resolved = getattr(completion, "model", None) or model
    return content, tid


def route_food_query(
    *,
    user_message: str,
    api_key: str,
    model: str,
    provider: Provider,
    base_url: str | None = None,
    tool_provider: str | None = None,
    tool_providers: set[str] | None = None,
    tool_provider_label: str | None = None,
    tool_provider_tools: list[str] | None = None,
) -> dict[str, Any]:
    if tool_providers:
        order_history = _order_history_plan(user_message, tool_providers)
        if order_history is not None:
            return order_history
        comparison = _comparison_plan(user_message, tool_providers)
        if comparison is not None:
            return comparison
    allowed_tools = (
        {tool for tool in tool_provider_tools if isinstance(tool, str) and tool.strip()}
        if tool_provider_tools
        else None
    )
    system_prompt = _system_prompt_for_tool_provider(
        tool_provider,
        tool_provider_label=tool_provider_label,
        tool_provider_tools=sorted(allowed_tools) if allowed_tools else None,
    )

    # =========================
    # OPENAI  &  DEEPSEEK
    # =========================

    if provider in ("openai", "deepseek"):
        content, tid = _openai_compatible_call(
            api_key=api_key,
            model=model,
            user_message=user_message,
            base_url=base_url if provider == "deepseek" else None,
            system_prompt=system_prompt,
        )

        data = _json_from_llm(content)

        return _finalize_from_parsed_json(
            data,
            provider=provider,
            model=model,
            trace_id=tid or "",
            tool_provider=tool_provider,
            allowed_tools=allowed_tools,
        )

    # =========================
    # GEMINI (google-genai package)
    # =========================
    try:
        from google import genai as google_genai
    except ImportError as e:
        raise RuntimeError(
            "Gemini provider requires the google-genai package: pip install google-genai"
        ) from e

    client = google_genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=f"""
SYSTEM:
{system_prompt}

USER:
{user_message.strip()}
""",
    )

    text = response.text or "{}"

    data = _json_from_llm(text)

    return _finalize_from_parsed_json(
        data,
        provider="gemini",
        model=model,
        trace_id="",
        tool_provider=tool_provider,
        allowed_tools=allowed_tools,
    )
