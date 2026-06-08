"""Zomato — the first clean `McpProvider` (replaces the LegacyMcpAdapter at cutover).

The base (`McpProvider`) drives the session/placeholder/execute loop; this plugin
supplies only what is Zomato-specific:

  * `stdio_spec`  — the mcp-remote launch for `mcp-server.zomato.com` (tokens hydrated).
  * `build_attachments` — menu + restaurant cards from real `ToolResult`s (golden-tested).
  * `merge` — fold this turn's restaurants into the session LRU.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge, Phase-8-removed).

Scope: restaurant + menu *search* (read-only) are ported + validated against real
data here. Cart/payment cards still come from the proven legacy executor path
(they need order-flow enrichment — cart-snapshot extraction, QR decode — and
fixtures that can't be captured without mutating real orders); the bootstrap swap
is gated on those landing so `dev` never regresses. See zomato/__init__ for status.
"""

from __future__ import annotations

import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.legacy_planner import legacy_plan
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.plugins.zomato.cards import build_restaurant_cards
from core.providers.plugins.zomato.menu_cards import build_menu_cards
from core.providers.plugins.zomato.session import ZomatoSession, merge_restaurants

_DEFAULT_URL = "https://mcp-server.zomato.com/mcp"


class ZomatoProvider(McpProvider):
    id = "zomato"
    label = "Zomato MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = ZomatoSession

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("ZOMATO_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        # Provider-internal render order: payment -> menu -> restaurant -> cart.
        # Menu + restaurant are ported clean; payment/cart stay on the executor
        # path (need order-flow enrichment + fixtures — see module docstring).
        return [
            *build_menu_cards(result.results),
            *build_restaurant_cards(result.results),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        restaurants = merge_restaurants(slice_.get("session_restaurants"), result.results)
        if restaurants:
            merged["session_restaurants"] = restaurants
        return merged

    async def plan(self, ctx: Any) -> Any:
        return await legacy_plan(self.id, ctx)
