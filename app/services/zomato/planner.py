"""Zomato domain agent: user message → JSON ``tool_plan`` for MCP (LLM + prompt only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.food_llm_router import complete_json_object, finalize_tool_plan
from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"

# Exact Zomato MCP tool names (must match tool_runtime.ZOMATO_PROVIDER.tools)
_ZOMATO_FOOD_TOOLS = frozenset(
    {
        "get_saved_addresses_for_user",
        "get_restaurants_for_keyword",
        "get_menu_items_listing",
        "get_restaurant_menu_by_categories",
        "create_cart",
        "get_cart_offers",
        "checkout_cart",
        "get_order_history",
        "get_order_tracking_info",
        "bind_user_number",
        "bind_user_number_verify_code",
    }
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return (_PROMPTS / "food.system.md").read_text(encoding="utf-8").strip()


def plan_zomato_food(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
    """Zomato Food agent turn: returns ``food_routing_plan``-shaped dict with ``tool_plan``.

    ``user_message`` should include conversation history when available (see
    ``agents.pipeline._format_routing_message``).
    """
    data = complete_json_object(
        user_message=user_message,
        system_prompt=_load_prompt(),
        api_key=llm.api_key,
        model=llm.model,
        provider=llm.provider,  # type: ignore[arg-type]
        base_url=llm.base_url,
    )
    return finalize_tool_plan(
        data,
        provider=llm.provider,  # type: ignore[arg-type]
        model=llm.model,
        trace_id="",
        allowed_tools=set(_ZOMATO_FOOD_TOOLS),
    )
