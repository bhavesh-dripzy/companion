"""Serp result tiles — dedicated card kinds per engine (flights, hotels,
places, products, videos, news), with the generic info_card as the fallback
for anything else (instagram profile, misc). Market data maps onto the
provider-agnostic `metric_card` (headline value + delta + series + stats —
reusable by any provider with a number to chart). Field names avoid the
Attachment interface collisions (`flight_options`, not `flights`;
`shop_products`, not `products` — that one belongs to Zepto)."""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.mcp.info_cards import build_info_cards

_KINDS: dict[str, tuple[str, str, str]] = {
    # tool -> (kind, data key in the lean envelope, attachment field)
    "web_search": ("web_search_cards", "results", "search_results"),
    "search_flights": ("flight_cards", "flights", "flight_options"),
    "search_hotels": ("hotel_cards", "hotels", "hotel_options"),
    "search_places": ("place_cards", "places", "place_results"),
    "search_products": ("shop_product_cards", "products", "shop_products"),
    "youtube_search": ("video_cards", "videos", "videos"),
    "search_shopping": ("shop_product_cards", "products", "shop_products"),
    "search_news": ("news_cards", "articles", "articles"),
}


def _rows(tr: ToolResult, data_key: str) -> list[dict[str, Any]]:
    envelope = tr.data if isinstance(tr.data, dict) else {}
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    rows = data.get(data_key)
    return rows if isinstance(rows, list) else []


def build_serp_cards(result: ProviderResult, provider_id: str) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in result.results:
        if tr.ok and tr.tool == "get_place_reviews":
            data = (tr.data or {}).get("data") or {}
            rows = [
                {
                    "critic": r.get("author"),
                    "publication": r.get("date"),
                    "rating": str(r.get("rating")) if r.get("rating") is not None else None,
                    "snippet": r.get("snippet"),
                }
                for r in data.get("reviews") or []
                if isinstance(r, dict)
            ]
            rows = [{k: v for k, v in r.items() if v} for r in rows if r.get("snippet")]
            if rows:
                out.append(
                    Attachment(
                        kind="review_cards",
                        provider="serp",
                        source=f"{provider_id}.{tr.tool}",
                        keyword=data.get("place"),
                        reviews=rows,
                    )
                )
            continue
        if tr.ok and tr.tool == "get_hotel_details":
            data = (tr.data or {}).get("data") or {}
            if data.get("name"):
                out.append(
                    Attachment(
                        kind="hotel_cards",
                        provider="serp",
                        source=f"{provider_id}.{tr.tool}",
                        keyword=data.get("name"),
                        hotel_options=[
                            {
                                "name": data.get("name"),
                                "rating": data.get("rating"),
                                "reviews": data.get("reviews"),
                                "rate_per_night": data.get("rate_per_night"),
                                "amenities": (data.get("amenities") or [])[:5],
                                "image": (data.get("images") or [None])[0],
                            }
                        ],
                    )
                )
            continue
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
        if tr.ok and tr.tool == "get_market_data":
            data = (tr.data or {}).get("data") or {}
            if data.get("name") or data.get("price"):
                metric = {
                    "title": data.get("name") or data.get("symbol"),
                    "subtitle": " · ".join(
                        str(v) for v in (data.get("symbol"), data.get("exchange")) if v
                    )
                    or None,
                    "value": data.get("price"),
                    "delta_percent": data.get("change_percent"),
                    "direction": str(data.get("movement") or "").lower() or None,
                    "series": data.get("spark"),
                    "series_label": "today",
                    "stats": data.get("key_stats"),
                    "as_of": data.get("as_of"),
                }
                out.append(
                    Attachment(
                        kind="metric_card",
                        provider="serp",
                        source=f"{provider_id}.{tr.tool}",
                        metric={k: v for k, v in metric.items() if v is not None},
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
        if tr.tool in (
            "web_search",
            "search_hotels",
            "search_places",
            "search_products",
            "search_shopping",
            "youtube_search",
        ):
            query = (tr.arguments or {}).get("query")
            if query:
                payload["keyword"] = str(query)
        if tr.tool == "search_shopping":
            payload["card_title"] = "Price comparison"
        out.append(Attachment(**payload))
    return out


def build_attachments(result: ProviderResult, provider_id: str) -> list[Attachment]:
    """Bespoke tiles for the five engines + info_card for everything else."""
    return [
        *build_serp_cards(result, provider_id),
        *build_info_cards(
            result.results,
            exclude=set(_KINDS)
            | {"get_product_details", "get_place_reviews", "get_hotel_details", "get_market_data"},
            provider_id=provider_id,
            brand="serp",
        ),
    ]
