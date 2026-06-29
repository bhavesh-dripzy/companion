"""Google Flights engine (SerpApi `google_flights`): params builder + lean extractor.

Pure functions. Round trips are a TWO-call flow: the first search returns outbound options each
carrying a `departure_token`; calling again with a chosen token fetches the return legs.
"""

from __future__ import annotations

from typing import Any

# Keep a deep pool (best + other) so the card's client-side filters (departure time, stops,
# airline) have real options to work with — the "best 8" alone are all peak-hour flights.
_MAX_ROWS = 30


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def flights_params(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "engine": "google_flights",
        "departure_id": str(args.get("departure_id") or "").strip().upper(),
        "arrival_id": str(args.get("arrival_id") or "").strip().upper(),
        "outbound_date": args.get("outbound_date"),
        "currency": "INR",
        "hl": "en",
        "gl": "in",
        # SerpApi: type=1 round trip (needs return_date), type=2 one way.
        "type": "1" if args.get("return_date") else "2",
    }
    if args.get("return_date"):
        params["return_date"] = args["return_date"]
    if args.get("departure_token"):
        params["departure_token"] = args["departure_token"]
    if args.get("adults"):
        params["adults"] = args["adults"]
    return params


def _leg(leg: dict[str, Any]) -> dict[str, Any]:
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
    """Lean flight options (best + other) with price, duration, stops, airlines, legs, and the
    `departure_token` needed for the return-leg follow-up. Plus a price-insight floor."""
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
                        "legs": [_leg(leg) for leg in legs[:4]],
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
        }
    )
