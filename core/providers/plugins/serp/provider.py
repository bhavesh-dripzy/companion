"""SerpProvider — managed public-data lookups via SerpApi (issue #46).

Flights, hotels, places, Amazon products, YouTube — one HTTP provider behind
`SERPAPI_KEY`, replacing the dead reverse-engineered scrapers. Results are
extracted lean at the boundary (see `engines`), cached with a TTL, and capped
by a daily budget (`client`). Instagram is the most ToS-sensitive engine and
ships OFF unless `SERP_INSTAGRAM_ENABLED=true`.
"""

from __future__ import annotations

import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.pipeline.clock import now_ist
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider
from core.providers.mcp.digest import lean_digest
from core.providers.plugins.serp import engines
from core.providers.plugins.serp.cards import build_attachments, flights_brief, merge_flights
from core.providers.plugins.serp.client import SerpBudgetExceeded, search
from core.providers.plugins.serp.tools import _INSTAGRAM_TOOL, _TOOLS

_DATE_ARGS = ("outbound_date", "return_date", "check_in_date", "check_out_date")


def _past_dates(args: dict[str, Any]) -> str:
    """Date args strictly before today (IST) — caught BEFORE the billed request;
    SerpApi rejects past travel dates and the call would just burn a credit."""
    today = now_ist().strftime("%Y-%m-%d")
    stale = [
        f"{name}={args[name]}"
        for name in _DATE_ARGS
        if isinstance(args.get(name), str) and args[name] < today
    ]
    return ", ".join(stale)


_PREAMBLE = (
    "You are the data-lookup planning agent (SerpApi-backed; read-only, no booking "
    "or purchasing). Translate the user's message into a machine-ready tool plan.\n"
    "Respond with ONLY a JSON object shaped exactly like:\n"
    '{"intent": "<snake_case>", "summary": "<one sentence>", '
    '"tool_plan": [{"tool": "<exact tool name>", "arguments": {<filled from the user\'s words>}}], '
    '"constraints": {"requires_address_resolution": false}}\n'
    "Fill EVERY required argument (marked * in the tool list) with literal values.\n"
    "- Dates MUST be YYYY-MM-DD; resolve relative dates ('next Friday') yourself.\n"
    "- search_flights needs IATA codes (Delhi→DEL, Mumbai→BOM, Bengaluru→BLR, "
    "Goa→GOI, Hyderabad→HYD, Chennai→MAA, Kolkata→CCU, Pune→PNQ, Jaipur→JAI).\n"
    "- Round-trip flights: the first call returns outbound options each with a "
    "departure_token; when the user picks one, call search_flights AGAIN with that "
    "departure_token (same route/dates) to fetch return legs.\n"
    "- These tools only FETCH data — never claim a booking or purchase happened.\n"
    "{{TOOLS}}"
)


def _instagram_enabled() -> bool:
    return os.getenv("SERP_INSTAGRAM_ENABLED", "false").strip().lower() == "true"


class SerpProvider(HttpProvider):
    id = "serp"
    label = "SerpApi Data"
    auth_mode = AuthMode.NONE

    planner_preamble = _PREAMBLE

    def declared_tools(self) -> list[Tool]:
        tools = list(_TOOLS)
        if _instagram_enabled():
            tools.append(_INSTAGRAM_TOOL)
        return tools

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        args = call.arguments if isinstance(call.arguments, dict) else {}
        missing = self._missing_required(call.tool, args)
        if missing:
            return self._error(
                call,
                ErrorCode.INVALID_ARGS,
                f"missing required arguments: {', '.join(missing)} — ask the user for them",
            )
        stale = _past_dates(args)
        if stale:
            today = now_ist().strftime("%Y-%m-%d")
            return self._error(
                call,
                ErrorCode.INVALID_ARGS,
                f"date(s) in the past: {stale} (today is {today}) — "
                "re-resolve the user's dates against today and retry",
            )
        try:
            params, extract = self._route(call.tool, args, ctx)
        except KeyError:
            return self._error(call, ErrorCode.INVALID_ARGS, f"unknown tool {call.tool}")
        try:
            payload = await search(params)
        except SerpBudgetExceeded as exc:
            return self._error(call, ErrorCode.UPSTREAM_ERROR, str(exc))
        except Exception as exc:
            return self._error(call, ErrorCode.UPSTREAM_ERROR, f"{type(exc).__name__}: {exc}")
        data = extract(payload)
        return ToolResult(
            tool=call.tool,
            provider=self.id,
            ok=True,
            arguments=args,
            data={"is_error": False, "data": data},
        )

    def _missing_required(self, tool_name: str, args: dict[str, Any]) -> list[str]:
        """Required-by-schema args the planner failed to fill — checked BEFORE the
        billed request so a malformed plan costs nothing."""
        tool = next((t for t in self.declared_tools() if t.name == tool_name), None)
        if tool is None:
            return []
        required = tool.params.get("required") or []
        return [name for name in required if str(args.get(name) or "").strip() == ""]

    def _route(self, tool: str, args: dict[str, Any], ctx: ExecCtx):
        if tool == "search_flights":
            return engines.flights_params(args), engines.extract_flights
        if tool == "search_hotels":
            return engines.hotels_params(args), engines.extract_hotels
        if tool == "search_places":
            return engines.places_params(args, ctx.user_geo), engines.extract_places
        if tool == "search_products":
            return engines.products_params(args), engines.extract_products
        if tool == "youtube_search":
            return engines.youtube_params(args), engines.extract_youtube
        if tool == "instagram_profile" and _instagram_enabled():
            return engines.instagram_params(args), engines.extract_instagram
        raise KeyError(tool)

    def _error(self, call: ToolCall, code: ErrorCode, detail: str) -> ToolResult:
        return ToolResult(
            tool=call.tool,
            provider=self.id,
            ok=False,
            arguments=call.arguments,
            data={"is_error": True, "data": {"message": detail}},
            error=code,
            error_detail=detail,
        )

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return build_attachments(result, self.id)

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return merge_flights(slice_, result)

    def planner_context(self, ctx: Any) -> str:
        legacy = ctx.session.legacy if isinstance(ctx.session.legacy, dict) else {}
        return flights_brief(legacy)

    def summarize_result(self, tool_result: Any) -> str | None:
        data = tool_result.data if isinstance(tool_result.data, dict) else None
        return lean_digest(data) if data else None
