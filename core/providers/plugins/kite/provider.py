"""Kite (Zerodha) — clean `McpProvider` (read-only market/account surfaces).

  * `stdio_spec` — mcp-remote launch for `mcp.kite.trade` (session-scoped login).
  * `build_attachments` — instrument / holdings / positions / open-orders / margin cards.
  * `merge` — instruments LRU + holdings/positions/orders replacement state.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge).

Auth: Kite uses TOOL auth — a `login` tool returns a Zerodha URL the user visits;
the session is then authenticated for read-only + (planner-gated) order tools.
The mutating order tools (place/modify/cancel) are NEVER auto-emitted here —
two-step order confirmation stays the planner's job (the pending order-ticket
card is round-tripped from session). Validated live (read-only) against a real
Zerodha account. Cutover is gated on the order-ticket round-trip + session parity.
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
from core.providers.plugins.kite.cards import build_attachments as build_kite_cards
from core.providers.plugins.kite.session import merge as merge_kite_session

_DEFAULT_URL = "https://mcp.kite.trade/mcp"


class KiteProvider(McpProvider):
    id = "kite"
    label = "Zerodha Kite MCP"
    auth_mode = AuthMode.TOOL
    session_cls = None  # multiple snapshot keys; merge writes flat session_kite_* keys

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("KITE_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return build_kite_cards(result.results)

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return merge_kite_session(slice_, result.results)

    async def plan(self, ctx: Any) -> Any:
        return await legacy_plan(self.id, ctx)
