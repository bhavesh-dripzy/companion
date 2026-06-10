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
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.plugins.district.cards import build_attachments as build_district_cards
from core.providers.plugins.district.placeholders import DistrictResolver
from core.providers.plugins.district.planner_context import build_context
from core.providers.plugins.district.reservation import (
    build_reservation_cards,
    reservation_snapshot,
)
from core.providers.plugins.district.session import merge as merge_district_session

_BOOKING_TOOLS = {"create_table_reservation_booking", "get_booking_details"}

_DEFAULT_URL = "https://mcp-server.district.in/mcp"

# Tools whose ONLY top-level args are location + filters; every other key the
# planner emits belongs inside filters=[{filter_name, filter_values}].
_FILTERED_SEARCH_TOOLS = frozenset(
    {
        "search_movies",
        "search_theatres",
        "search_shows",
        "search_events",
        "search_event_venues",
        "search_artists",
    }
)

_FILTERED_TOP_LEVEL_KEYS = frozenset({"location", "filters"})


def _filter_value(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


class DistrictProvider(McpProvider):
    id = "district"
    label = "District MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = None  # multiple entity lists; merge writes flat session_* keys
    resolver = DistrictResolver()

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        url = os.getenv("DISTRICT_MCP_URL", _DEFAULT_URL)
        return remote_stdio_spec(url, user_id=ctx.user_id)

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Re-nest stray top-level keys (movie_name, movie_id, show_date, …) into
        the filters array — the server rejects unknown top-level keys outright."""
        if tool not in _FILTERED_SEARCH_TOOLS or not isinstance(args, dict):
            return args
        stray = {k: v for k, v in args.items() if k not in _FILTERED_TOP_LEVEL_KEYS}
        if not stray:
            return args
        out = {k: v for k, v in args.items() if k in _FILTERED_TOP_LEVEL_KEYS}
        existing = out.get("filters")
        filters = list(existing) if isinstance(existing, list) else []
        for key, value in stray.items():
            values = value if isinstance(value, list) else [value]
            filters.append(
                {"filter_name": key, "filter_values": [_filter_value(v) for v in values]}
            )
        out["filters"] = filters
        return out

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        """Seed __USER_LAT__/__USER_LNG__ for search tools, from GPS or geocoding.

        The planner emits these as placeholder strings (planner.md); resolved to
        floats here from ctx.user_geo, else by geocoding the locality the user
        named. Unresolved placeholders make the District server reject the call."""
        import asyncio

        from companion_api.google_maps import geocode_address

        await super().before_run(session, ctx)
        geo = ctx.user_geo if isinstance(ctx.user_geo, dict) else {}
        lat = geo.get("latitude", geo.get("lat"))
        lng = geo.get("longitude", geo.get("lng"))
        if (lat is None or lng is None) and ctx.requested_location_text:
            point = await asyncio.to_thread(
                geocode_address, ctx.requested_location_text, region="in"
            )
            if point is not None:
                lat, lng = point.latitude, point.longitude
        if lat is None or lng is None:
            return
        try:
            ctx.placeholders["__USER_LAT__"] = float(lat)
            ctx.placeholders["__USER_LNG__"] = float(lng)
        except (TypeError, ValueError):
            return

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

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)
