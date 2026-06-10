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

import json
import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.info_cards import build_info_cards
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.plugins.kite.cards import build_attachments as build_kite_cards
from core.providers.plugins.kite.planner_context import build_context
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
        return [
            *build_kite_cards(result.results),
            *build_info_cards(
                result.results,
                exclude={
                    "search_instruments",
                    "get_ltp",
                    "get_ohlc",
                    "get_quotes",
                    "get_holdings",
                    "get_positions",
                    "get_orders",
                    "get_margins",
                    "get_gtts",
                    "get_trades",
                    "get_mf_holdings",
                    "get_historical_data",
                    "login",
                },
                provider_id=self.id,
                brand="kite",
            ),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return merge_kite_session(slice_, result.results)

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)

    def summarize_result(self, tool_result: Any) -> str | None:
        """`login` returns the auth URL the user must open — the generic lean
        digest strips URLs, so pass that envelope through raw."""
        if tool_result.tool == "login":
            data = tool_result.data
            return json.dumps(data)[:1200] if data is not None else None
        return super().summarize_result(tool_result)
