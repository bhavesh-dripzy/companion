"""Swiggy Food session slice + merge (restaurant LRU, shared with Zomato)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts.session import ProviderSessionState
from core.contracts.tool_result import ToolResult
from core.providers.mcp.food_session import lean, lru_merge
from core.providers.mcp.lru import lru_merge as lru_merge_keyed
from core.providers.plugins.swiggy_food.cards import restaurant_entries
from core.providers.plugins.swiggy_food.menu_cards import menu_entries

_SEARCH_TOOLS = {"search_restaurants"}
_MENU_TOOLS = {"search_menu", "get_restaurant_menu"}
_MAX_MENU_ITEMS = 35


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


def merge_menu(
    prior: list[dict[str, Any]] | None, results: list[ToolResult]
) -> list[dict[str, Any]]:
    """session_menu LRU (variant_id→item) so the planner can create_cart by item name."""
    fresh: list[dict[str, Any]] = []
    for tr in results:
        if tr.tool not in _MENU_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        fresh.extend(menu_entries(envelope))
    return lru_merge_keyed(prior, fresh, key=lambda e: e.get("variant_id"), cap=_MAX_MENU_ITEMS)
