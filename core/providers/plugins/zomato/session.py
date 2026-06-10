"""Zomato session slice + merge.

The planner reuses a small LRU of `(name, res_id)` pairs it saw across the
conversation (`session_restaurants`) to resolve "order from the second one" /
"that Domino's" without re-searching. The LRU merge is shared with Swiggy
(`mcp.food_session`); this module supplies Zomato's fresh-entry extraction.

The flat `session_restaurants` key (not a nested provider slice) is kept so the
frontend echo + the legacy planner read it unchanged during migration.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts.session import ProviderSessionState
from core.contracts.tool_result import ToolResult
from core.providers.mcp.food_session import lean, lru_merge
from core.providers.plugins.zomato.cards import restaurant_entries

_SEARCH_TOOLS = {"get_restaurants_for_keyword"}


class ZomatoSession(ProviderSessionState):
    """Zomato's namespaced session slice (restaurant LRU; menu/cart added as ported)."""

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
    return lru_merge(prior, fresh, provider="zomato")
