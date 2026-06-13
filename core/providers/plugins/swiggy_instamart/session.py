"""Swiggy Instamart session slice + merge (product LRU keyed by spinId)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts.session import ProviderSessionState
from core.contracts.tool_result import ToolResult
from core.providers.mcp.lru import lru_merge
from core.providers.plugins.swiggy_instamart.cards import products_from_search

# Matches the executor's _MAX_REMEMBERED_PRODUCTS so the planner's spinId LRU agrees.
_MAX_PRODUCTS = 60
_SEARCH_TOOLS = {"search_products", "your_go_to_items"}


class SwiggyInstamartSession(ProviderSessionState):
    """Instamart's namespaced session slice (product LRU; cart added as ported)."""

    products: list[dict[str, Any]] = Field(default_factory=list)


def merge_products(
    prior: list[dict[str, Any]] | None, results: list[ToolResult]
) -> list[dict[str, Any]]:
    fresh: list[dict[str, Any]] = []
    for tr in results:
        if tr.tool not in _SEARCH_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        query = str((tr.arguments or {}).get("query") or "").strip() or None
        fresh.extend(products_from_search(envelope, query=query))
    return lru_merge(prior, fresh, key=lambda e: e.get("product_id"), cap=_MAX_PRODUCTS)
