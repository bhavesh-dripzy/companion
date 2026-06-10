"""Zomato — the clean `McpProvider` (the sole Zomato path).

The base (`McpProvider`) drives the session/placeholder/execute loop and the
generated planner (`planner.md` + `planner_context`); this plugin supplies only
what is Zomato-specific:

  * `stdio_spec`  — the mcp-remote launch for `mcp-server.zomato.com` (tokens hydrated).
  * `build_attachments` — payment + menu + restaurant + cart cards from real `ToolResult`s.
  * `merge` — fold this turn's restaurants (LRU) + cart (replacement) into the session.

The full rendering layer is clean: restaurant/menu search cards + cart/payment
cards (cart-snapshot extraction + QR decode are pure functions of the envelopes).
Follow-up create_cart-by-item-name uses the `session_menu` menu_index
(variant→price/variants/addon schema).
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
from core.providers.plugins.zomato.cards import build_restaurant_cards
from core.providers.plugins.zomato.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.zomato.digest import summarize
from core.providers.plugins.zomato.menu_cards import build_menu_cards
from core.providers.plugins.zomato.menu_index import merge_menu
from core.providers.plugins.zomato.payment_cards import build_payment_cards
from core.providers.plugins.zomato.planner_context import build_context
from core.providers.plugins.zomato.session import ZomatoSession, merge_restaurants

_DEFAULT_URL = "https://mcp-server.zomato.com/mcp"

_SEARCH_FILTER_KEYS = (
    "min_price",
    "max_price",
    "min_rating",
    "is_gourmet",
    "new_for_you_filter",
    "offers_tag",
    "near_and_fast",
    "distance",
    "flash_sale",
)

_MENU_FILTER_KEYS = ("min_price", "max_price")

# tool -> (nested filter field, keys the planner tends to flatten to top level).
_FILTER_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "get_restaurants_for_keyword": ("filter", _SEARCH_FILTER_KEYS),
    "get_restaurant_menu_by_categories": ("menu_filter", _MENU_FILTER_KEYS),
    "get_menu_items_listing": ("menu_filter", _MENU_FILTER_KEYS),
}


class ZomatoProvider(McpProvider):
    id = "zomato"
    label = "Zomato MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = ZomatoSession
    saved_addresses_tool = "get_saved_addresses_for_user"

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("ZOMATO_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Re-nest filter keys the planner left at top level. Every Zomato tool
        is `additionalProperties: false`, so one stray key rejects the call."""
        spec = _FILTER_FIELDS.get(tool)
        if not spec or not isinstance(args, dict):
            return args
        field, keys = spec
        stray = {k: args[k] for k in keys if k in args}
        if not stray:
            return args
        out = {k: v for k, v in args.items() if k not in stray}
        nested = out.get(field)
        out[field] = {**stray, **(nested if isinstance(nested, dict) else {})}
        return out

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
        merge_saved_addresses(merged, result.results, provider=self.id)
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

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)

    def summarize_result(self, tool_result: Any) -> str | None:
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        return summarize(tool_result.tool, data)
