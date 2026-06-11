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
from core.providers.plugins.serp import more_engines as m2
from core.providers.plugins.serp.cards import build_attachments
from core.providers.plugins.serp.client import SerpBudgetExceeded, search
from core.providers.plugins.serp.product_detail import (
    extract_product_details,
    product_details_params,
)
from core.providers.plugins.serp.session import (
    flights_brief,
    hotels_brief,
    merge_serp_session,
    places_brief,
    products_brief,
)
from core.providers.plugins.serp.tools import _INSTAGRAM_TOOL, _TOOLS
from core.providers.plugins.serp.tools_more import MORE_TOOLS

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
    '"constraints": {"requires_address_resolution": false, '
    '"requested_location_text": "<city/locality the user named, else null>"}}\n'
    "Fill EVERY required argument (marked * in the tool list) with literal values.\n"
    "- Set constraints.requested_location_text whenever the user names a city or "
    "locality — it geo-biases place searches; null when they mean 'near me'.\n"
    "- Dates MUST be YYYY-MM-DD; resolve relative dates ('next Friday') yourself.\n"
    "- search_flights needs IATA codes (Delhi→DEL, Mumbai→BOM, Bengaluru→BLR, "
    "Goa→GOI, Hyderabad→HYD, Chennai→MAA, Kolkata→CCU, Pune→PNQ, Jaipur→JAI).\n"
    "- Round-trip flights: the first call returns outbound options each with a "
    "departure_token; when the user picks one, call search_flights AGAIN with that "
    "departure_token (same route/dates) to fetch return legs.\n"
    "- get_place_reviews / get_product_details / get_hotel_details are the DEEP-DIVE "
    "tools: use literal ids/tokens from results or the session brief (query fallback "
    "exists for places and products). get_market_data is general market info; the "
    "user's own portfolio belongs to the Kite provider, never here.\n"
    "- For details/reviews/images of ONE product, use get_product_details with the "
    "literal asin from search results or the session brief (or query as fallback).\n"
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
        tools = [*_TOOLS, *MORE_TOOLS]
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
        if call.tool == "get_place_reviews":
            args = dict(args)
            if not str(args.get("data_id") or "").strip():
                query = str(args.get("query") or "").strip()
                if not query:
                    return self._error(
                        call, ErrorCode.INVALID_ARGS, "need data_id or query for place reviews"
                    )
                try:
                    hits = await search(engines.places_params({"query": query}, ctx.user_geo))
                    rows = hits.get("local_results") or (
                        [hits["place_results"]]
                        if isinstance(hits.get("place_results"), dict)
                        else []
                    )
                    args["data_id"] = str((rows[0] or {}).get("data_id") or "")
                except Exception as exc:
                    return self._error(call, ErrorCode.UPSTREAM_ERROR, f"lookup failed: {exc}")
                if not args["data_id"]:
                    return self._error(
                        call, ErrorCode.UPSTREAM_ERROR, f"no place match for {query!r}"
                    )
        if call.tool == "get_product_details":
            args = dict(args)
            if not str(args.get("asin") or "").strip():
                query = str(args.get("query") or "").strip()
                if not query:
                    return self._error(
                        call, ErrorCode.INVALID_ARGS, "need asin or query for product details"
                    )
                try:
                    hits = await search(engines.products_params({"query": query}))
                    rows = hits.get("organic_results") or [{}]
                    args["asin"] = str(rows[0].get("asin") or "")
                except Exception as exc:
                    return self._error(call, ErrorCode.UPSTREAM_ERROR, f"lookup failed: {exc}")
                if not args["asin"]:
                    return self._error(
                        call, ErrorCode.UPSTREAM_ERROR, f"no Amazon match for {query!r}"
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
        if tool == "get_product_details":
            return product_details_params(args), extract_product_details
        if tool == "search_news":
            return m2.news_params(args), m2.extract_news
        if tool == "get_market_data":
            return m2.finance_params(args), m2.extract_finance
        if tool == "search_shopping":
            return m2.shopping_params(args), m2.extract_shopping
        if tool == "search_events":
            return m2.events_params(args), m2.extract_events
        if tool == "get_place_reviews":
            return m2.place_reviews_params(args), m2.extract_place_reviews
        if tool == "get_hotel_details":
            return m2.hotel_details_params(args), m2.extract_hotel_details
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
        return merge_serp_session(slice_, result)

    def planner_context(self, ctx: Any) -> str:
        legacy = ctx.session.legacy if isinstance(ctx.session.legacy, dict) else {}
        briefs = (
            flights_brief(legacy),
            products_brief(legacy),
            places_brief(legacy),
            hotels_brief(legacy),
        )
        return "\n\n".join(b for b in briefs if b)

    def summarize_result(self, tool_result: Any) -> str | None:
        data = tool_result.data if isinstance(tool_result.data, dict) else None
        return lean_digest(data) if data else None
