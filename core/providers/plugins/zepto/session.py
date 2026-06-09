"""Zepto session slice + merge (product LRU keyed by productVariantId)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts.session import ProviderSessionState
from core.contracts.tool_result import ToolResult
from core.providers.mcp.lru import lru_merge
from core.providers.plugins.zepto.cards import products_from_search

_MAX_PRODUCTS = 60
_SEARCH_TOOLS = {"search_products", "search_multiple_products", "get_past_order_items"}


class ZeptoSession(ProviderSessionState):
    """Zepto's namespaced session slice (product LRU; cart added as ported)."""

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
    return lru_merge(prior, fresh, key=lambda e: e.get("product_variant_id"), cap=_MAX_PRODUCTS)
