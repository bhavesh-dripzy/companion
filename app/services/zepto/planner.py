"""Zepto domain agent: user message → JSON ``tool_plan`` for MCP."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.food_llm_router import complete_json_object, finalize_tool_plan
from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"

# Exact tool surface exposed by the live Zepto MCP (see
# ``outputs/zepto/_zepto_tools_catalog.json`` for the source of truth).
_ZEPTO_TOOLS = frozenset(
    {
        # Discovery
        "search_products",
        "search_multiple_products",
        "get_product_details",
        "get_past_order_items",
        "zepto_shop",
        # Location & store
        "get_location_serviceability",
        "list_saved_addresses",
        "select_saved_address",
        "add_saved_address",
        "update_drop_zone",
        "select_store",
        # Cart
        "view_cart",
        "update_cart",
        # User profile
        "get_user_details",
        "update_user_name",
        # Orders
        "list_order_history",
        "get_order_detail",
        # Payment & order placement (kept in scope; the prompt guards
        # against firing these without explicit user confirmation).
        "get_payment_methods",
        "create_order",
        "create_online_payment_order",
        "create_wallet_order",
        "create_upi_reserve_pay_order",
        "check_payment_status",
    }
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return (_PROMPTS / "food.system.md").read_text(encoding="utf-8").strip()


def plan_zepto(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
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
        allowed_tools=set(_ZEPTO_TOOLS),
    )
