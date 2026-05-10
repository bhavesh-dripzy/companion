"""
Maps free-text food requests → a JSON plan typed for downstream Zomato MCP tool calls.

Downstream replaces the placeholder __ADDRESS_ID__ with an address_id from get_saved_addresses_for_user.
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

_SYSTEM_PROMPT = """\
You translate the user's message into a machine-ready plan for the Zomato MCP server tools.

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
- get_saved_addresses_for_user
- get_restaurants_for_keyword
- get_restaurant_menu_by_categories
- get_menu_items_listing
- create_cart
- get_cart_offers
- checkout_cart
- get_order_history
- get_order_tracking_info
- bind_user_number
- bind_user_number_verify_code

RULES:
1. Typical flow for discovery:
   get_saved_addresses_for_user
   THEN
   get_restaurants_for_keyword

2. If user asks for food near them:
   ALWAYS include BOTH tools.

3. Use "__ADDRESS_ID__" placeholder.

4. Never invent restaurant IDs or variant IDs.

5. Return ONLY valid JSON.
"""


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
