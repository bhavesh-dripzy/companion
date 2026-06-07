"""Kite (Zerodha) domain agent: user message → JSON ``tool_plan`` for MCP (LLM + prompt only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.food_llm_router import complete_json_object, finalize_tool_plan
from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"

# Exact Kite MCP tool names — the full surface, read-only AND mutating. The
# two-step confirmation that gates the mutating tools lives in the prompt, not
# here; the planner is the only layer allowed to emit a mutating tool, and only
# after explicit user confirmation.
_KITE_TOOLS = frozenset(
    {
        # Auth
        "login",
        # Account / profile
        "get_profile",
        "get_margins",
        "get_holdings",
        "get_positions",
        "get_mf_holdings",
        # Orders & trades (read)
        "get_orders",
        "get_order_history",
        "get_order_trades",
        "get_trades",
        "get_gtts",
        # Market data
        "get_historical_data",
        "search_instruments",
        "get_ltp",
        "get_ohlc",
        "get_quotes",
        # Mutating — order placement / modification (two-step confirmation only)
        "place_order",
        "modify_order",
        "cancel_order",
        "place_gtt_order",
        "modify_gtt_order",
        "delete_gtt_order",
    }
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return (_PROMPTS / "trading.system.md").read_text(encoding="utf-8").strip()


def plan_kite(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
    """Kite agent turn: returns ``food_routing_plan``-shaped dict with ``tool_plan``.

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
        allowed_tools=set(_KITE_TOOLS),
    )
