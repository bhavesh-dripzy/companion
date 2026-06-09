"""District — clean `McpProvider` (dining / movies / theatres / events / venues / artists).

  * `stdio_spec` — mcp-remote launch for `mcp-server.district.in` (tokens hydrated).
  * `build_attachments` — typed discovery cards (dining/movie/theatre/show/event/venue/artist).
  * `merge` — fold this turn's entities into per-type session LRUs.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge).

Scope: the read-only discovery surfaces are ported + validated against real
District data. The reservation card (create_table_reservation_booking) is a
mutating booking and stays on the executor path — gated like the food carts — so
the bootstrap swap doesn't regress. District search tools take a numeric
`{latitude, longitude}` location, which the legacy parity planner supplies.
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
from core.providers.plugins.district.cards import build_attachments as build_district_cards
from core.providers.plugins.district.reservation import (
    build_reservation_cards,
    reservation_snapshot,
)
from core.providers.plugins.district.session import merge as merge_district_session

_BOOKING_TOOLS = {"create_table_reservation_booking", "get_booking_details"}

_DEFAULT_URL = "https://mcp-server.district.in/mcp"


class DistrictProvider(McpProvider):
    id = "district"
    label = "District MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = None  # multiple entity lists; merge writes flat session_* keys

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("DISTRICT_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return [
            *build_district_cards(result.results),
            *build_reservation_cards(result.results),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = merge_district_session(slice_, result.results)
        for tr in result.results:
            if tr.tool in _BOOKING_TOOLS and tr.ok:
                snap = reservation_snapshot(
                    tr.data if isinstance(tr.data, dict) else {}, args=tr.arguments
                )
                if snap:
                    merged["session_reservation"] = snap
        return merged

    async def plan(self, ctx: Any) -> Any:
        return await legacy_plan(self.id, ctx)
