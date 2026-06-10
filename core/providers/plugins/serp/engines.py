"""Per-engine SerpApi param mapping + LEAN extraction: raw ~100KB envelopes
become tool results small enough for the narrator, planner brief, and tiles."""

from __future__ import annotations

from typing import Any

_MAX_ROWS = 8


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def flights_params(args: dict[str, Any]) -> dict[str, Any]:
    params = {
        "engine": "google_flights",
        "departure_id": str(args.get("departure_id") or "").strip().upper(),
        "arrival_id": str(args.get("arrival_id") or "").strip().upper(),
        "outbound_date": args.get("outbound_date"),
        "currency": "INR",
        "hl": "en",
        "gl": "in",
        "type": "1" if args.get("return_date") else "2",
    }
    if args.get("return_date"):
        params["return_date"] = args["return_date"]
    if args.get("departure_token"):
        params["departure_token"] = args["departure_token"]
    if args.get("adults"):
        params["adults"] = args["adults"]
    return params


def _flight_leg(leg: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "from": (leg.get("departure_airport") or {}).get("id"),
            "departs": (leg.get("departure_airport") or {}).get("time"),
            "to": (leg.get("arrival_airport") or {}).get("id"),
            "arrives": (leg.get("arrival_airport") or {}).get("time"),
            "airline": leg.get("airline"),
            "flight_number": leg.get("flight_number"),
        }
    )


def extract_flights(payload: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for bucket in ("best_flights", "other_flights"):
        for option in payload.get(bucket) or []:
            if not isinstance(option, dict) or len(rows) >= _MAX_ROWS:
                continue
            legs = [leg for leg in option.get("flights") or [] if isinstance(leg, dict)]
            rows.append(
                _clean(
                    {
                        "price": option.get("price"),
                        "total_duration_minutes": option.get("total_duration"),
                        "stops": max(len(legs) - 1, 0),
                        "airlines": sorted(
                            {leg.get("airline") for leg in legs if leg.get("airline")}
                        ),
                        "legs": [_flight_leg(leg) for leg in legs[:4]],
                        "airline_logo": option.get("airline_logo"),
                        "departure_token": option.get("departure_token"),
                        "booking_token": option.get("booking_token"),
                    }
                )
            )
    insights = payload.get("price_insights") or {}
    return _clean(
        {
            "flights": rows,
            "lowest_price": insights.get("lowest_price"),
            "price_level": insights.get("price_level"),
            "note": (
                "Round trip: pick a flight and call search_flights again with its "
                "departure_token to get return legs."
                if rows and rows[0].get("departure_token")
                else None
            ),
        }
    )


def hotels_params(args: dict[str, Any]) -> dict[str, Any]:
    params = {
        "engine": "google_hotels",
        "q": args.get("query"),
        "check_in_date": args.get("check_in_date"),
        "check_out_date": args.get("check_out_date"),
        "currency": "INR",
        "hl": "en",
        "gl": "in",
    }
    if args.get("adults"):
        params["adults"] = args["adults"]
    return params


def extract_hotels(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for prop in (payload.get("properties") or [])[:_MAX_ROWS]:
        if not isinstance(prop, dict):
            continue
        rows.append(
            _clean(
                {
                    "name": prop.get("name"),
                    "rate_per_night": (prop.get("rate_per_night") or {}).get("lowest"),
                    "total_rate": (prop.get("total_rate") or {}).get("lowest"),
                    "hotel_class": prop.get("hotel_class"),
                    "rating": prop.get("overall_rating"),
                    "reviews": prop.get("reviews"),
                    "amenities": (prop.get("amenities") or [])[:5],
                    "image": ((prop.get("images") or [{}])[0] or {}).get("thumbnail"),
                    "link": prop.get("link"),
                    "property_token": prop.get("property_token"),
                }
            )
        )
    return {"hotels": rows}


def places_params(args: dict[str, Any], user_geo: dict[str, Any] | None) -> dict[str, Any]:
    params = {"engine": "google_maps", "type": "search", "q": args.get("query"), "hl": "en"}
    geo = user_geo or {}
    lat = geo.get("latitude", geo.get("lat"))
    lng = geo.get("longitude", geo.get("lng"))
    if lat is not None and lng is not None and not args.get("location_text"):
        params["ll"] = f"@{lat},{lng},14z"
    elif args.get("location_text"):
        params["q"] = f"{args.get('query')} near {args['location_text']}"
    return params


def extract_places(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    results = payload.get("local_results") or []
    if isinstance(payload.get("place_results"), dict):
        results = [payload["place_results"]]
    for place in results[:_MAX_ROWS]:
        if not isinstance(place, dict):
            continue
        rows.append(
            _clean(
                {
                    "name": place.get("title"),
                    "rating": place.get("rating"),
                    "reviews": place.get("reviews"),
                    "category": place.get("type"),
                    "address": place.get("address"),
                    "open_state": place.get("open_state"),
                    "phone": place.get("phone"),
                    "price": place.get("price"),
                    "image": place.get("thumbnail"),
                    "website": place.get("website"),
                }
            )
        )
    return {"places": rows}


def products_params(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": "amazon",
        "k": args.get("query"),
        "amazon_domain": "amazon.in",
    }


def extract_products(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for item in (payload.get("organic_results") or [])[:_MAX_ROWS]:
        if not isinstance(item, dict):
            continue
        rows.append(
            _clean(
                {
                    "name": item.get("title"),
                    "price": (item.get("price") or {}).get("raw")
                    if isinstance(item.get("price"), dict)
                    else item.get("price"),
                    "rating": item.get("rating"),
                    "reviews": item.get("reviews"),
                    "asin": item.get("asin"),
                    "prime": item.get("prime"),
                    "image": item.get("thumbnail"),
                    "link": item.get("link_clean") or item.get("link"),
                }
            )
        )
    return {"products": rows}


def youtube_params(args: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "youtube", "search_query": args.get("query")}


def extract_youtube(payload: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for video in (payload.get("video_results") or [])[:_MAX_ROWS]:
        if not isinstance(video, dict):
            continue
        rows.append(
            _clean(
                {
                    "title": video.get("title"),
                    "channel": (video.get("channel") or {}).get("name"),
                    "views": video.get("views"),
                    "length": video.get("length"),
                    "published": video.get("published_date"),
                    "link": video.get("link"),
                    "image": (video.get("thumbnail") or {}).get("static")
                    if isinstance(video.get("thumbnail"), dict)
                    else video.get("thumbnail"),
                }
            )
        )
    return {"videos": rows}


def instagram_params(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": "instagram_profile",
        "profile_id": str(args.get("username") or "").strip().lstrip("@"),
    }


def extract_instagram(payload: dict[str, Any]) -> dict[str, Any]:
    profile = (
        payload.get("profile_results")
        if isinstance(payload.get("profile_results"), dict)
        else payload
    )
    return _clean(
        {
            "username": profile.get("username"),
            "name": profile.get("full_name") or profile.get("name"),
            "followers": profile.get("followers"),
            "following": profile.get("following"),
            "posts": profile.get("posts_count") or profile.get("posts"),
            "bio": profile.get("biography") or profile.get("bio"),
            "verified": profile.get("is_verified"),
            "private": profile.get("is_private"),
        }
    )
