"""
Maps free-text food requests to a JSON plan typed for downstream MCP tool calls.

Downstream can resolve placeholders such as __ADDRESS_ID__ and __RESTAURANT_ID__
from earlier tool results in the same plan.
"""

from __future__ import annotations

import json
import logging
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
    }
)

_ADDRESS_TOOLS = {"get_restaurants_for_keyword"}
_RESTAURANT_TOOLS = {"get_restaurant_menu_by_categories", "get_menu_items_listing"}
_ORDER_TOOLS = {"get_order_tracking_info"}

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
- get_restaurant_menu_by_categories: accepts restaurant_id or res_id. Use "__RESTAURANT_ID__" only after a restaurant-search step.
- get_menu_items_listing: accepts restaurant_id or res_id. Use "__RESTAURANT_ID__" only after a restaurant-search step.
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

4. For menu questions without a known restaurant id, plan:
   get_saved_addresses_for_user
   get_restaurants_for_keyword
   get_restaurant_menu_by_categories with {"restaurant_id": "__RESTAURANT_ID__"}

5. For item searches inside one restaurant, use get_menu_items_listing after restaurant discovery.

6. Never invent address IDs, restaurant IDs, menu item IDs, variant IDs, cart IDs, or order IDs.

7. Prefer useful multi-step plans over a single tool when later tools can consume IDs from earlier tools.

8. Return ONLY valid JSON.
"""


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


def _ensure_prerequisites(
    tool_plan: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    if not tool_plan:
        return tool_plan

    needs_address = any(step.get("tool") in _ADDRESS_TOOLS for step in tool_plan)
    needs_restaurant = any(step.get("tool") in _RESTAURANT_TOOLS for step in tool_plan)

    out = list(tool_plan)
    if (needs_address or needs_restaurant) and not _has_tool(out, "get_saved_addresses_for_user"):
        out.insert(0, {"tool": "get_saved_addresses_for_user", "arguments": {}})

    if needs_restaurant and not _has_tool(out, "get_restaurants_for_keyword"):
        insert_at = 1 if _has_tool(out, "get_saved_addresses_for_user") else 0
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


def normalize_tool_plan(tool_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for step in tool_plan:
        if not isinstance(step, dict):
            continue

        tool = step.get("tool")
        arguments = step.get("arguments") or {}

        if not isinstance(tool, str):
            continue

        if tool not in _ALLOWED_TOOLS:
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

        if tool in _RESTAURANT_TOOLS:
            arguments = dict(arguments)
            restaurant_id = (
                arguments.get("restaurant_id")
                or arguments.get("res_id")
                or arguments.get("restaurantId")
                or arguments.get("resId")
            )
            if restaurant_id is None or (isinstance(restaurant_id, str) and not restaurant_id.strip()):
                arguments["restaurant_id"] = "__RESTAURANT_ID__"

        if tool in _ORDER_TOOLS:
            arguments = dict(arguments)
            order_id = arguments.get("order_id") or arguments.get("orderId")
            if order_id is None or (isinstance(order_id, str) and not order_id.strip()):
                arguments["order_id"] = "__ORDER_ID__"

        out.append(
            {
                "tool": tool.strip(),
                "arguments": arguments,
            }
        )

    return out


def _finalize_from_parsed_json(
    data: dict[str, Any],
    *,
    provider: Provider,
    model: str,
    trace_id: str,
) -> dict[str, Any]:
    tool_plan_raw = data.get("tool_plan")

    tool_plan = normalize_tool_plan(
        tool_plan_raw if isinstance(tool_plan_raw, list) else []
    )

    constraints = data.get("constraints")

    if constraints is None or not isinstance(constraints, dict):
        constraints = {}
    constraints.setdefault("requested_location_text", None)
    tool_plan = _ensure_prerequisites(tool_plan, data=data, constraints=constraints)
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
            {"role": "system", "content": _SYSTEM_PROMPT},
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
) -> dict[str, Any]:

    # =========================
    # OPENAI  &  DEEPSEEK
    # =========================

    if provider in ("openai", "deepseek"):
        content, tid = _openai_compatible_call(
            api_key=api_key,
            model=model,
            user_message=user_message,
            base_url=base_url if provider == "deepseek" else None,
        )

        data = _json_from_llm(content)

        return _finalize_from_parsed_json(
            data,
            provider=provider,
            model=model,
            trace_id=tid or "",
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
{_SYSTEM_PROMPT}

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
    )
