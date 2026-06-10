"""Serp session slices + planner briefs: flight tokens, product asins, place
data_ids, hotel property_tokens — the literal ids follow-up turns need."""

from __future__ import annotations

from typing import Any

from core.contracts.tool_result import ProviderResult
from core.providers.plugins.serp.cards import _rows


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
        elif tr.tool == "search_places":
            rows = _rows(tr, "places")
            if rows:
                merged["session_serp_places"] = [
                    {k: row.get(k) for k in ("name", "data_id", "rating") if row.get(k)}
                    for row in rows[:8]
                ]
        elif tr.tool == "search_hotels":
            rows = _rows(tr, "hotels")
            args = tr.arguments or {}
            if rows:
                merged["session_serp_hotels"] = [
                    {
                        "name": row.get("name"),
                        "property_token": row.get("property_token"),
                        "query": args.get("query"),
                        "check_in_date": args.get("check_in_date"),
                        "check_out_date": args.get("check_out_date"),
                    }
                    for row in rows[:6]
                    if row.get("property_token")
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


def places_brief(legacy: dict[str, Any]) -> str:
    """Planner brief: shown places with literal data_ids for get_place_reviews."""
    rows = legacy.get("session_serp_places")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = [
        f"  - {str(row.get('name'))[:60]} → data_id={row.get('data_id')}"
        for row in rows[:8]
        if isinstance(row, dict) and row.get("data_id")
    ]
    if not lines:
        return ""
    return (
        "Places already shown — for actual review texts of one, call "
        "get_place_reviews with its literal data_id:\n" + "\n".join(lines)
    )


def hotels_brief(legacy: dict[str, Any]) -> str:
    """Planner brief: shown hotels with tokens + the search args details needs."""
    rows = legacy.get("session_serp_hotels")
    if not isinstance(rows, list) or not rows:
        return ""
    lines = []
    for row in rows[:6]:
        if not isinstance(row, dict) or not row.get("property_token"):
            continue
        lines.append(
            f"  - {str(row.get('name'))[:60]} → property_token={row['property_token']} "
            f"(query={row.get('query')!r}, check_in_date={row.get('check_in_date')}, "
            f"check_out_date={row.get('check_out_date')})"
        )
    if not lines:
        return ""
    return (
        "Hotels already shown — for a deep dive on one, call get_hotel_details "
        "with its literal property_token AND the same query/dates:\n" + "\n".join(lines)
    )
