"""Google Hotels engine (SerpApi `google_hotels`): params builder + lean extractor. Pure functions."""

from __future__ import annotations

from typing import Any

_MAX_ROWS = 8


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def hotels_params(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
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


def extract_hotels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Lean hotel rows: name, nightly/total rate (INR), class, rating, reviews, amenities, image."""
    rows: list[dict[str, Any]] = []
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
    return rows
