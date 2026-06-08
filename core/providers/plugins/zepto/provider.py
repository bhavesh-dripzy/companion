"""Zepto — clean `McpProvider` (grocery; structured product search + LRU).

  * `stdio_spec` — mcp-remote launch for `mcp.zepto.co.in` (tokens hydrated).
  * `build_attachments` — zepto_product_cards from real `ToolResult`s.
  * `merge` — fold this turn's products into the productVariantId LRU.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge).

Scope: product *search* (read-only) is ported + validated against real Zepto data.
Zepto requires a store to be selected first (get_location_serviceability /
select_store); the legacy domain planner handles that setup chain, which the
parity bridge reuses. Cart cards stay on the executor path; bootstrap swap gated.
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
from core.providers.plugins.zepto.cards import build_product_cards
from core.providers.plugins.zepto.session import ZeptoSession, merge_products

_DEFAULT_URL = "https://mcp.zepto.co.in/mcp"


class ZeptoProvider(McpProvider):
    id = "zepto"
    label = "Zepto MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = ZeptoSession

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("ZEPTO_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return build_product_cards(result.results)

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        products = merge_products(slice_.get("session_zepto_products"), result.results)
        if products:
            merged["session_zepto_products"] = products
        return merged

    async def plan(self, ctx: Any) -> Any:
        return await legacy_plan(self.id, ctx)
