"""BlaBlaCarProvider — intercity carpool search deep-links for India (HTTP provider).

No credentials (Tier 1): builds a `blablacar.in` search deep-link for the user's
route + date + seats, anchoring origin/destination to Google Place IDs (the same
identifiers BlaBlaCar's own autocomplete uses) when Maps is configured, and
falling back to plain place names otherwise. Live results and booking happen in
BlaBlaCar — this hands the user a one-tap search. Mirrors the `maps` plugin's
sole-provider HTTP shape (ADR-0002).
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider

_SEARCH_BASE = "https://www.blablacar.in/search"

_TOOL = Tool(
    name="search_carpools",
    provider="blablacar",
    description=(
        "Find shared intercity CAR rides (carpools) between two Indian cities on "
        "BlaBlaCar — e.g. 'carpool from Delhi to Jaipur tomorrow', 'shared ride to "
        "Chandigarh'. Returns a ready-to-open BlaBlaCar search for the route, date "
        "and number of seats. Not for local cabs/taxis."
    ),
    params={
        "type": "object",
        "properties": {
            "origin": {"type": "string", "description": "Departure city/place, e.g. 'New Delhi'"},
            "destination": {
                "type": "string",
                "description": "Arrival city/place, e.g. 'Karnal'",
            },
            "date": {
                "type": "string",
                "description": "Travel date as YYYY-MM-DD; omit for today.",
            },
            "seats": {"type": "integer", "description": "Seats needed (default 1)."},
        },
        "required": ["origin", "destination"],
    },
    auth_mode=AuthMode.NONE,
    source="static",
)


def _bbc_place_id(google_place_id: str) -> str:
    """Wrap a Google Place ID into BlaBlaCar's `place_id` envelope (base64 JSON).

    Matches their autocomplete encoding exactly: base64 of
    ``{"i": <google_place_id>, "p":1, "v":1, "t":[4]}`` (t=4 is a city/locality)."""
    payload = {"i": google_place_id, "p": 1, "v": 1, "t": [4]}
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _india_today() -> str:
    """Today's date in India (IST) as YYYY-MM-DD — the carpool date default."""
    return (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date().isoformat()


def _normalize_date(value: Any) -> str:
    """A YYYY-MM-DD travel date: the parsed input if recognizable, else today (IST)."""
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _india_today()


def _place_ids(origin: str, destination: str) -> tuple[str | None, str | None]:
    """Google Place IDs for both endpoints (best-effort; None when Maps is absent)."""
    try:
        from companion_api.google_maps import geocode_place_id
    except Exception:
        return None, None
    try:
        return geocode_place_id(origin, region="in"), geocode_place_id(destination, region="in")
    except Exception:
        return None, None


def _build_deeplink(origin: str, destination: str, travel_date: str, seats: int) -> str:
    """The blablacar.in search URL for this route — anchored to Place IDs when available."""
    params: dict[str, str] = {
        "fn": origin,
        "tn": destination,
        "db": travel_date,
        "seats": str(seats),
    }
    from_pid, to_pid = _place_ids(origin, destination)
    if from_pid and to_pid:
        params["from_place_id"] = _bbc_place_id(from_pid)
        params["to_place_id"] = _bbc_place_id(to_pid)
    return f"{_SEARCH_BASE}?{urlencode(params)}"


def _compute(args: dict[str, Any]) -> dict[str, Any]:
    """Build the carpool-search result dict (the deep-link the narrator/card surface)."""
    origin = str(args.get("origin") or "").strip()
    destination = str(args.get("destination") or "").strip()
    if not origin or not destination:
        return {
            "ok": False,
            "error": "I need both a from-city and a to-city to search carpools.",
            "error_code": "missing_route",
        }
    raw_seats = args.get("seats")
    try:
        seats = max(1, int(raw_seats)) if raw_seats is not None else 1
    except (TypeError, ValueError):
        seats = 1
    travel_date = _normalize_date(args.get("date"))
    return {
        "ok": True,
        "origin": origin,
        "destination": destination,
        "date": travel_date,
        "seats": seats,
        "deeplink": _build_deeplink(origin, destination, travel_date, seats),
        "provider_label": "BlaBlaCar",
    }


class BlaBlaCarProvider(HttpProvider):
    id = "blablacar"
    label = "BlaBlaCar"
    auth_mode = AuthMode.NONE
    session_cls = None

    def declared_tools(self) -> list[Tool]:
        return [_TOOL]

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        if call.tool != "search_carpools":
            raise InvalidArgsError(f"unknown blablacar tool: {call.tool}")
        result = await asyncio.to_thread(_compute, call.arguments)
        ok = bool(result.get("ok"))
        return ToolResult(
            tool=call.tool,
            provider=self.id,
            ok=ok,
            arguments=dict(call.arguments),
            data=result,
            error=None if ok else ErrorCode.INVALID_ARGS,
            error_detail=None if ok else str(result.get("error") or ""),
        )

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        cards: list[Attachment] = []
        for tr in result.results:
            data = tr.data if tr.ok and isinstance(tr.data, dict) else None
            if data and data.get("deeplink"):
                cards.append(
                    Attachment(
                        kind="blablacar_card",
                        provider=self.id,
                        origin=data.get("origin"),
                        destination=data.get("destination"),
                        date=data.get("date"),
                        seats=data.get("seats"),
                        url=data.get("deeplink"),
                    )
                )
        return cards
