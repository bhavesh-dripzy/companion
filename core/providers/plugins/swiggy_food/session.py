"""Swiggy Food session slice + merge (restaurant LRU, shared with Zomato)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts.session import ProviderSessionState
from core.contracts.tool_result import ToolResult
from core.providers.mcp.food_session import lean, lru_merge
from core.providers.plugins.swiggy_food.cards import restaurant_entries

_SEARCH_TOOLS = {"search_restaurants"}


class SwiggyFoodSession(ProviderSessionState):
    """Swiggy Food's namespaced session slice (restaurant LRU; menu/cart as ported)."""

    restaurants: list[dict[str, Any]] = Field(default_factory=list)


def merge_restaurants(
    prior: list[dict[str, Any]] | None, results: list[ToolResult]
) -> list[dict[str, Any]]:
    fresh: list[dict[str, Any]] = []
    for tr in results:
        if tr.tool not in _SEARCH_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        fresh.extend(lean(e) for e in restaurant_entries(envelope))
    return lru_merge(prior, fresh)
