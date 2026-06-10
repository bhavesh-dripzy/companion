"""Swiggy Food — clean `McpProvider` (second food provider, same pattern as Zomato).

The base drives the session/placeholder/execute loop; this plugin supplies the
Swiggy-specific bits:

  * `stdio_spec` — the mcp-remote launch for `mcp.swiggy.com/food` (tokens hydrated).
  * `build_attachments` — menu + restaurant cards from real `ToolResult`s (golden-tested).
  * `merge` — fold this turn's restaurants into the session LRU (shared helper).
  * `plan` — delegate to the tuned legacy domain agent (parity bridge, Phase-8-removed).

Scope: restaurant + menu *search* (read-only) are ported + validated against real
Swiggy data. Cart cards still come from the proven legacy executor (order-flow
enrichment + no safe fixture); the bootstrap swap is gated on those so `dev` never
regresses. Swiggy's dual-key address handling (addressId/address_id) is covered by
the shared placeholder resolver's `__ADDRESS_ID__` aliases.
"""

from __future__ import annotations

import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.address import merge_saved_addresses
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.plugins.swiggy_food.cards import build_restaurant_cards
from core.providers.plugins.swiggy_food.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.swiggy_food.digest import summarize
from core.providers.plugins.swiggy_food.menu_cards import build_menu_cards
from core.providers.plugins.swiggy_food.placeholders import SwiggyFoodResolver
from core.providers.plugins.swiggy_food.planner_context import build_context
from core.providers.plugins.swiggy_food.session import (
    SwiggyFoodSession,
    merge_menu,
    merge_restaurants,
)

_CART_TOOLS = {"get_food_cart", "update_food_cart"}

_DEFAULT_URL = "https://mcp.swiggy.com/food"


class SwiggyFoodProvider(McpProvider):
    id = "swiggy_food"
    label = "Swiggy Food MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = SwiggyFoodSession
    saved_addresses_tool = "get_addresses"
    resolver = SwiggyFoodResolver()

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("SWIGGY_FOOD_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        # Internal render order: menu -> restaurant -> cart.
        return [
            *build_menu_cards(result.results),
            *build_restaurant_cards(result.results),
            *build_cart_cards(result.results),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        merge_saved_addresses(merged, result.results, provider=self.id)
        restaurants = merge_restaurants(slice_.get("session_restaurants"), result.results)
        if restaurants:
            merged["session_restaurants"] = restaurants
        menu = merge_menu(slice_.get("session_menu"), result.results)
        if menu:
            merged["session_menu"] = menu
        # Single-restaurant replacement cart: the latest cart envelope wins.
        for tr in result.results:
            if tr.tool in _CART_TOOLS and tr.ok:
                snap = cart_snapshot(
                    tr.data if isinstance(tr.data, dict) else {}, args=tr.arguments
                )
                if snap:
                    merged["session_cart"] = snap
        return merged

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)

    def summarize_result(self, tool_result: Any) -> str | None:
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        return summarize(tool_result.tool, data)
