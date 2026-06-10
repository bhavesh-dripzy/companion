"""Serp result tiles — dedicated card kinds per engine (flights, hotels,
places, products, videos), with the generic info_card as the fallback for
anything else (instagram profile, misc). Field names avoid the Attachment
interface collisions (`flight_options`, not `flights`; `shop_products`, not
`products` — that one belongs to Zepto)."""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.mcp.info_cards import build_info_cards

_KINDS: dict[str, tuple[str, str, str]] = {
    # tool -> (kind, data key in the lean envelope, attachment field)
    "search_flights": ("flight_cards", "flights", "flight_options"),
    "search_hotels": ("hotel_cards", "hotels", "hotel_options"),
    "search_places": ("place_cards", "places", "place_results"),
    "search_products": ("shop_product_cards", "products", "shop_products"),
    "youtube_search": ("video_cards", "videos", "videos"),
}


def _rows(tr: ToolResult, data_key: str) -> list[dict[str, Any]]:
    envelope = tr.data if isinstance(tr.data, dict) else {}
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    rows = data.get(data_key)
    return rows if isinstance(rows, list) else []


def build_serp_cards(result: ProviderResult, provider_id: str) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in result.results:
        if tr.ok and tr.tool == "get_product_details":
            detail = (tr.data or {}).get("data") or {}
            if detail.get("name"):
                out.append(
                    Attachment(
                        kind="shop_product_cards",
                        provider="serp",
                        source=f"{provider_id}.{tr.tool}",
                        keyword=detail.get("name"),
                        shop_products=[
                            {
                                k: detail.get(k)
                                for k in (
                                    "name",
                                    "price",
                                    "rating",
                                    "reviews",
                                    "asin",
                                    "image",
                                    "link",
                                )
                                if detail.get(k) is not None
                            }
                        ],
                    )
                )
            continue
        if not tr.ok or tr.tool not in _KINDS:
            continue
        kind, data_key, field = _KINDS[tr.tool]
        rows = _rows(tr, data_key)
        if not rows:
            continue
        payload: dict[str, Any] = {
            "kind": kind,
            "provider": "serp",
            "source": f"{provider_id}.{tr.tool}",
            field: rows,
        }
        if tr.tool == "search_flights":
            data = (tr.data or {}).get("data") or {}
            if data.get("lowest_price"):
                payload["lowest_price"] = data["lowest_price"]
            args = tr.arguments or {}
            route = f"{args.get('departure_id', '')} → {args.get('arrival_id', '')}".strip(" →")
            if route:
                payload["route"] = route
            for date_field in ("outbound_date", "return_date"):
                if args.get(date_field):
                    payload[date_field] = args[date_field]
            if args.get("departure_token"):
                payload["leg"] = "return"
        if tr.tool in ("search_hotels", "search_places", "search_products", "youtube_search"):
            query = (tr.arguments or {}).get("query")
            if query:
                payload["keyword"] = str(query)
        out.append(Attachment(**payload))
    return out


def build_attachments(result: ProviderResult, provider_id: str) -> list[Attachment]:
    """Bespoke tiles for the five engines + info_card for everything else."""
    return [
        *build_serp_cards(result, provider_id),
        *build_info_cards(
            result.results,
            exclude=set(_KINDS) | {"get_product_details"},
            provider_id=provider_id,
            brand="serp",
        ),
    ]


def merge_serp_session(slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
    """Latest flight options (tokens for leg 2) and product hits (asins for
    detail lookups) into the session for follow-up turns."""
    merged = dict(slice_)
    for tr in result.results:
        if not tr.ok:
            continue
        if tr.tool == "search_flights":
            rows = _rows(tr, "flights")
            if rows:
                merged["session_serp_flights"] = [
                    {k: v for k, v in row.items() if k != "airline_logo"} for row in rows[:6]
                ]
        elif tr.tool == "search_products":
            rows = _rows(tr, "products")
            if rows:
                merged["session_serp_products"] = [
                    {k: row.get(k) for k in ("name", "asin", "price") if row.get(k)}
                    for row in rows[:8]
                ]
    return merged


def flights_brief(legacy: dict[str, Any]) -> str:
    """Planner brief: the shown flight options with their literal tokens."""
    rows = legacy.get("session_serp_flights")
    if not isinstance(rows, list) or not rows:
        return ""
    lines: list[str] = []
    for i, row in enumerate(rows[:6], start=1):
        if not isinstance(row, dict):
            continue
        legs = row.get("legs") or []
        first, last = (legs[0] if legs else {}), (legs[-1] if legs else {})
        bits = [
            f"₹{row.get('price')}" if row.get("price") else None,
            "/".join(row.get("airlines") or []) or None,
            f"{first.get('from', '?')} {first.get('departs', '')} → {last.get('to', '?')}".strip(),
            f"{row.get('stops', 0)} stop(s)",
        ]
        line = f"  {i}. " + ", ".join(b for b in bits if b)
        token = row.get("departure_token")
        if token:
            line += f" [departure_token={token}]"
        lines.append(line)
    if not lines:
        return ""
    return (
        "Flight options already shown to the user (most recent search). When the "
        "user picks one for a ROUND TRIP, call search_flights again with the SAME "
        "route/dates plus that option's literal departure_token to fetch return "
        "legs:\n" + "\n".join(lines)
    )


def products_brief(legacy: dict[str, Any]) -> str:
    """Planner brief: shown products with literal asins for get_product_details."""
    rows = legacy.get("session_serp_products")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        f"  - {str(row.get('name'))[:70]} → asin={row.get('asin')}"
        for row in rows[:8]
        if isinstance(row, dict) and row.get("asin")
    ]
    if not lines:
        return ""
    return (
        "Products already shown to the user — for details/reviews of one, call "
        "get_product_details with its literal asin:\n" + "\n".join(lines)
    )
