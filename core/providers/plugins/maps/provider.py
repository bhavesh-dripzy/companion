"""MapsProvider — distance/ETA via the Google Routes API (HTTP provider).

Ports `app/services/maps/executor.execute_maps_query`, calling the persistent
`companion_api.google_maps` infra directly. Needs the caller's geo (ctx.user_geo).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider

_TOOL = Tool(
    name="get_directions",
    provider="maps",
    description="Distance and ETA from the user's location to a destination.",
    params={
        "type": "object",
        "properties": {
            "destination": {"type": "string", "description": "Place name or address"},
            "travel_mode": {"type": "string", "enum": ["DRIVE", "WALK", "BICYCLE", "TRANSIT"]},
        },
        "required": ["destination"],
    },
    auth_mode=AuthMode.NONE,
    source="static",
)


def _compute(args: dict[str, Any], user_geo: dict[str, Any] | None) -> dict[str, Any]:
    from companion_api.google_maps import (
        GeoPoint,
        GoogleMapsError,
        compute_route,
        google_maps_configured,
    )

    providers = ["google_maps"]
    if not google_maps_configured():
        return {
            "ok": False,
            "error": "Google Maps is not configured.",
            "error_code": "not_configured",
        }
    geo = user_geo if isinstance(user_geo, dict) else {}
    lat, lng = geo.get("latitude", geo.get("lat")), geo.get("longitude", geo.get("lng"))
    if lat is None or lng is None:
        return {
            "ok": False,
            "error": "I need your current location.",
            "error_code": "needs_location",
        }
    destination = str(args.get("destination") or "").strip()
    if not destination:
        return {"ok": False, "error": "No destination found.", "error_code": "missing_destination"}
    travel_mode = args.get("travel_mode") if isinstance(args.get("travel_mode"), str) else None
    try:
        route = compute_route(
            origin=GeoPoint(float(lat), float(lng)),
            destination=destination,
            travel_mode=travel_mode,
        )
    except GoogleMapsError as exc:
        return {"ok": False, "error": str(exc), "error_code": exc.code, "destination": destination}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "error_code": "api_error"}
    return {
        "ok": True,
        "destination": destination,
        "travel_mode": travel_mode or "DRIVE",
        "route": route.to_dict(),
        "routed_providers": providers,
    }


class MapsProvider(HttpProvider):
    id = "maps"
    label = "Google Maps"
    auth_mode = AuthMode.NONE
    session_cls = None

    def declared_tools(self) -> list[Tool]:
        return [_TOOL]

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        if call.tool != "get_directions":
            raise InvalidArgsError(f"unknown maps tool: {call.tool}")
        result = await asyncio.to_thread(_compute, call.arguments, ctx.user_geo)
        ok = bool(result.get("ok"))
        return ToolResult(
            tool=call.tool,
            provider="maps",
            ok=ok,
            arguments=dict(call.arguments),
            data=result,
            error=None if ok else ErrorCode.UPSTREAM_ERROR,
            error_detail=None if ok else str(result.get("error") or ""),
        )

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        cards = []
        for tr in result.results:
            route = (tr.data or {}).get("route") if tr.ok else None
            if route:
                cards.append(
                    Attachment(kind="route_card", destination=tr.data.get("destination"), **route)
                )
        return cards
