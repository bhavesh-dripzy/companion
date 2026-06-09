"""Zomato — the first clean `McpProvider` (replaces the LegacyMcpAdapter at cutover).

The base (`McpProvider`) drives the session/placeholder/execute loop; this plugin
supplies only what is Zomato-specific:

  * `stdio_spec`  — the mcp-remote launch for `mcp-server.zomato.com` (tokens hydrated).
  * `build_attachments` — payment + menu + restaurant + cart cards from real `ToolResult`s.
  * `merge` — fold this turn's restaurants (LRU) + cart (replacement) into the session.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge, Phase-8-removed).

Scope: the full RENDERING layer (restaurant/menu search cards + cart/payment cards)
is ported clean. Cart-snapshot extraction + QR decode are pure functions of the
respective envelopes, so those cards build without the executor. The bootstrap swap
LegacyMcpAdapter→ZomatoProvider remains gated on the SESSION layer — `session_menu`
(the executor's menu_index: variant→price/variants/addon schema the planner needs to
create_cart by item name on follow-ups). Until that lands, dev keeps the adapter.
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
from core.providers.plugins.zomato.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.zomato.menu_cards import build_menu_cards
from core.providers.plugins.zomato.menu_index import merge_menu
from core.providers.plugins.zomato.payment_cards import build_payment_cards
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
        # Provider-internal render order (parity with the legacy hoist):
        #   payment -> menu -> restaurant -> cart.
        return [
            *build_payment_cards(result.results),
            *build_menu_cards(result.results),
            *build_restaurant_cards(result.results),
            *build_cart_cards(result.results),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        restaurants = merge_restaurants(slice_.get("session_restaurants"), result.results)
        if restaurants:
            merged["session_restaurants"] = restaurants
        # session_menu: the variant→item index the planner reuses to create_cart
        # by item name on a follow-up turn (from menu + keyword-search envelopes).
        menu = merge_menu(slice_.get("session_menu"), result.results)
        if menu:
            merged["session_menu"] = menu
        # Zomato carts are single-restaurant replacement state: the latest
        # create_cart this turn overwrites session_cart whole.
        for tr in result.results:
            if tr.tool == "create_cart" and tr.ok:
                snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {})
                if snap:
                    merged["session_cart"] = snap
        return merged

    async def plan(self, ctx: Any) -> Any:
        return await legacy_plan(self.id, ctx)
