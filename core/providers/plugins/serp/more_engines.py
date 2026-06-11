"""Batch-2 SerpApi engines: news, finance, shopping, events, place reviews,
hotel property details. Params + lean extractors, validated against live
payloads (2026-06-11). YouTube transcripts are NOT here — SerpApi exposes no
transcript engine (probed: `youtube_transcripts` unsupported; `youtube_video`
carries no transcript field)."""

from __future__ import annotations

from typing import Any

_MAX_ROWS = 8
_MAX_REVIEWS = 6


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def _trim(text: Any, cap: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= cap else value[: cap - 1] + "…"


def news_params(args: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "google_news", "q": args.get("query"), "gl": "in", "hl": "en"}


def extract_news(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in (payload.get("news_results") or [])[:_MAX_ROWS]:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        rows.append(
            _clean(
                {
                    "title": _trim(item.get("title"), 140),
                    "source": source.get("name") if isinstance(source, dict) else source,
                    "date": item.get("date"),
                    "link": item.get("link"),
                    "image": item.get("thumbnail"),
                }
            )
        )
    return {"articles": rows}


def finance_params(args: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "google_finance", "q": args.get("symbol"), "hl": "en"}


_SPARK_POINTS = 48


def _spark(payload: dict[str, Any]) -> list[float]:
    """Downsample the intraday graph series (~376 points) to a lean sparkline."""
    prices = [
        p["price"]
        for p in (payload.get("graph") or [])
        if isinstance(p, dict) and isinstance(p.get("price"), int | float)
    ]
    if len(prices) <= _SPARK_POINTS:
        return [round(p, 2) for p in prices]
    step = (len(prices) - 1) / (_SPARK_POINTS - 1)
    return [round(prices[int(i * step)], 2) for i in range(_SPARK_POINTS)]


def extract_finance(payload: dict[str, Any]) -> dict[str, Any]:
    """Lean google_finance snapshot; change_percent is SIGNED (SerpApi ships the
    magnitude in price_movement.percentage and the sign in .movement)."""
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    movement = (
        summary.get("price_movement") if isinstance(summary.get("price_movement"), dict) else {}
    )
    kg = payload.get("knowledge_graph") if isinstance(payload.get("knowledge_graph"), dict) else {}
    stats = {}
    for row in (
        kg.get("key_stats", {}).get("stats", []) if isinstance(kg.get("key_stats"), dict) else []
    ):
        if isinstance(row, dict) and row.get("label") and len(stats) < 6:
            stats[row["label"]] = row.get("value")
    headlines = [
        _trim(n.get("snippet") or n.get("title"), 110)
        for n in (payload.get("news_results") or [])[:3]
        if isinstance(n, dict)
    ]
    pct = movement.get("percentage")
    if isinstance(pct, int | float):
        sign = -1 if str(movement.get("movement", "")).lower() == "down" else 1
        pct = round(sign * abs(pct), 2)
    return _clean(
        {
            "name": summary.get("title"),
            "symbol": summary.get("stock"),
            "exchange": summary.get("exchange"),
            "price": summary.get("extracted_price") or summary.get("price"),
            "change_percent": pct,
            "movement": movement.get("movement"),
            "as_of": summary.get("date"),
            "spark": _spark(payload) or None,
            "key_stats": stats or None,
            "headlines": [h for h in headlines if h] or None,
        }
    )


def shopping_params(args: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "google_shopping", "q": args.get("query"), "gl": "in", "hl": "en"}


def extract_shopping(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in (payload.get("shopping_results") or [])[:_MAX_ROWS]:
        if not isinstance(item, dict):
            continue
        rows.append(
            _clean(
                {
                    "name": _trim(item.get("title"), 110),
                    "price": item.get("price"),
                    "rating": item.get("rating"),
                    "reviews": item.get("reviews"),
                    "source": item.get("source"),
                    "delivery": item.get("delivery"),
                    "image": item.get("thumbnail"),
                    "link": item.get("product_link"),
                }
            )
        )
    return {"products": rows}


def events_params(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if args.get("location_text"):
        query = f"{query} in {args['location_text']}"
    return {"engine": "google_events", "q": query, "gl": "in", "hl": "en"}


def extract_events(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in (payload.get("events_results") or [])[:_MAX_ROWS]:
        if not isinstance(item, dict):
            continue
        date = item.get("date") if isinstance(item.get("date"), dict) else {}
        venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        address = item.get("address")
        rows.append(
            _clean(
                {
                    "title": _trim(item.get("title"), 110),
                    "when": date.get("when") or date.get("start_date"),
                    "venue": venue.get("name"),
                    "address": ", ".join(address[:2]) if isinstance(address, list) else address,
                    "description": _trim(item.get("description"), 140) or None,
                    "link": item.get("link"),
                    "image": item.get("thumbnail") or item.get("image"),
                }
            )
        )
    return {"events": rows}


def place_reviews_params(args: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "google_maps_reviews", "data_id": args.get("data_id"), "hl": "en"}


def extract_place_reviews(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("place_info") if isinstance(payload.get("place_info"), dict) else {}
    rows = []
    for item in (payload.get("reviews") or [])[:_MAX_REVIEWS]:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        rows.append(
            _clean(
                {
                    "rating": item.get("rating"),
                    "snippet": _trim(item.get("snippet") or item.get("extracted_snippet"), 220),
                    "date": item.get("date"),
                    "author": user.get("name"),
                    "likes": item.get("likes"),
                }
            )
        )
    return _clean(
        {
            "place": info.get("title"),
            "rating": info.get("rating"),
            "total_reviews": info.get("reviews"),
            "reviews": rows,
        }
    )


def hotel_details_params(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": "google_hotels",
        "q": args.get("query"),
        "check_in_date": args.get("check_in_date"),
        "check_out_date": args.get("check_out_date"),
        "property_token": args.get("property_token"),
        "currency": "INR",
        "hl": "en",
        "gl": "in",
    }


def extract_hotel_details(payload: dict[str, Any]) -> dict[str, Any]:
    images = [
        (img or {}).get("thumbnail")
        for img in (payload.get("images") or [])[:5]
        if isinstance(img, dict)
    ]
    rate = payload.get("rate_per_night") if isinstance(payload.get("rate_per_night"), dict) else {}
    return _clean(
        {
            "name": payload.get("name"),
            "description": _trim(payload.get("description"), 320) or None,
            "rating": payload.get("overall_rating"),
            "reviews": payload.get("reviews"),
            "location_rating": payload.get("location_rating"),
            "rate_per_night": rate.get("lowest"),
            "check_in": payload.get("check_in_time"),
            "check_out": payload.get("check_out_time"),
            "amenities": (payload.get("amenities") or [])[:8],
            "images": [i for i in images if i] or None,
            "nearby": [
                _trim((p or {}).get("name"), 60)
                for p in (payload.get("nearby_places") or [])[:4]
                if isinstance(p, dict)
            ]
            or None,
        }
    )
