"""Swiggy Instamart — clean `McpProvider` (grocery; product search + LRU).

Supplies the Instamart-specific bits; the base drives the execute loop:

  * `stdio_spec` — mcp-remote launch for `mcp.swiggy.com/im` (tokens hydrated).
  * `build_attachments` — instamart_product_cards from real `ToolResult`s.
  * `merge` — fold this turn's products into the spinId LRU.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge).

Scope: product *search* (read-only) is ported + validated against real Instamart
data. Cart cards stay on the executor path (get_cart snapshot enrichment); the
bootstrap swap is gated on those + session parity so dev never regresses.
"""

from __future__ import annotations

import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.plugins.swiggy_instamart.cards import build_product_cards
from core.providers.plugins.swiggy_instamart.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.swiggy_instamart.planner_context import build_context
from core.providers.plugins.swiggy_instamart.session import SwiggyInstamartSession, merge_products

_CART_TOOLS = {"get_cart", "update_cart", "clear_cart"}

_DEFAULT_URL = "https://mcp.swiggy.com/im"


class SwiggyInstamartProvider(McpProvider):
    id = "swiggy_instamart"
    label = "Swiggy Instamart MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = SwiggyInstamartSession

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("SWIGGY_INSTAMART_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return [*build_product_cards(result.results), *build_cart_cards(result.results)]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        products = merge_products(slice_.get("session_instamart_products"), result.results)
        if products:
            merged["session_instamart_products"] = products
        for tr in result.results:
            if tr.tool in _CART_TOOLS and tr.ok:
                snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {})
                if snap:
                    merged["session_instamart_cart"] = snap
        return merged

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)
