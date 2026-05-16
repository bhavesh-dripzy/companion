"""Instamart domain agent: user message → JSON ``tool_plan`` for MCP (LLM + prompt only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.food_llm_router import complete_json_object, finalize_tool_plan
from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"

# Exact Instamart MCP tool names (must match tool_runtime.SWIGGY_INSTAMART_PROVIDER.tools)
_INSTAMART_GROCERY_TOOLS = frozenset(
    {
        "get_addresses",
        "search_products",
        "your_go_to_items",
        "get_cart",
        "update_cart",
        "clear_cart",
        "checkout",
        "get_orders",
        "track_order",
        "report_error",
    }
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return (_PROMPTS / "food.system.md").read_text(encoding="utf-8").strip()


def plan_instamart(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
    """Instamart agent turn: returns ``food_routing_plan``-shaped dict with ``tool_plan``.

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
        allowed_tools=set(_INSTAMART_GROCERY_TOOLS),
    )
