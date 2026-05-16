"""Swiggy domain agents: user message → JSON ``tool_plan`` for MCP (LLM + prompt only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.food_llm_router import complete_json_object, finalize_tool_plan
from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"

_SWIGGY_FOOD_TOOLS = frozenset(
    {
        "get_addresses",
        "search_restaurants",
        "search_menu",
        "get_restaurant_menu",
        "get_food_cart",
        "update_food_cart",
        "flush_food_cart",
        "place_food_order",
        "fetch_food_coupons",
        "apply_food_coupon",
        "get_food_orders",
        "get_food_order_details",
        "track_food_order",
        "report_error",
    }
)

_SWIGGY_INSTAMART_TOOLS = frozenset(
    {
        "get_addresses",
        "search_products",
        "get_product_details",
        "get_cart",
        "add_item_to_cart",
        "update_cart_item",
        "remove_cart_item",
        "clear_cart",
        "get_cart_offers",
        "checkout",
        "place_order",
        "get_orders",
        "get_order_details",
        "report_error",
    }
)


@lru_cache(maxsize=2)
def _load_prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8").strip()


def plan_swiggy_food(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
    """Swiggy Food agent turn: returns ``food_routing_plan``-shaped dict with ``tool_plan``.

    ``user_message`` should include conversation history when available (see
    ``agents.pipeline._format_routing_message``).
    """
    data = complete_json_object(
        user_message=user_message,
        system_prompt=_load_prompt("food.system.md"),
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
        allowed_tools=set(_SWIGGY_FOOD_TOOLS),
    )


def plan_swiggy_instamart(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
    data = complete_json_object(
        user_message=user_message,
        system_prompt=_load_prompt("instamart.system.md"),
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
        allowed_tools=set(_SWIGGY_INSTAMART_TOOLS),
    )
