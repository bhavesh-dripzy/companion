"""Serp tool declarations (JSON-Schema params) + the planner preamble."""

from __future__ import annotations

from core.contracts.tool import AuthMode, Tool

_DATE = {"type": "string", "description": "YYYY-MM-DD"}

_TOOLS: list[Tool] = [
    Tool(
        name="search_flights",
        provider="serp",
        description=(
            "Google Flights search: prices, airlines, durations, stops. One-way by "
            "default; pass return_date for round trips. Round trip is TWO calls: the "
            "first returns outbound options each carrying a departure_token — call "
            "again with the chosen departure_token to get return legs. No booking."
        ),
        params={
            "type": "object",
            "properties": {
                "departure_id": {"type": "string", "description": "IATA airport code, e.g. DEL"},
                "arrival_id": {"type": "string", "description": "IATA airport code, e.g. BOM"},
                "outbound_date": _DATE,
                "return_date": _DATE,
                "departure_token": {
                    "type": "string",
                    "description": "From a prior search_flights result; fetches return legs",
                },
                "adults": {"type": "integer"},
            },
            "required": ["departure_id", "arrival_id", "outbound_date"],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    ),
    Tool(
        name="search_hotels",
        provider="serp",
        description=(
            "Google Hotels search: nightly/total rates in INR, star class, ratings, "
            "amenities. Query is free text ('hotels in Goa near Baga beach')."
        ),
        params={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "check_in_date": _DATE,
                "check_out_date": _DATE,
                "adults": {"type": "integer"},
            },
            "required": ["query", "check_in_date", "check_out_date"],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    ),
    Tool(
        name="search_places",
        provider="serp",
        description=(
            "Google Maps place search: businesses/landmarks with rating, address, "
            "open-now state, phone. Uses the user's GPS when no location is named."
        ),
        params={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "location_text": {
                    "type": "string",
                    "description": "Named area ('Hauz Khas') ONLY when the user names one",
                },
            },
            "required": ["query"],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    ),
    Tool(
        name="search_products",
        provider="serp",
        description="Amazon.in product search: titles, prices, ratings, Prime flag, ASINs.",
        params={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    ),
    Tool(
        name="youtube_search",
        provider="serp",
        description="YouTube search: videos with channel, views, length, link.",
        params={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    ),
]

_TOOLS.append(
    Tool(
        name="get_product_details",
        provider="serp",
        description=(
            "Amazon.in product page for ONE product: price/discount/stock/delivery, "
            "image gallery, feature bullets, spec table, and a review-sentiment "
            "summary with per-aspect positives/negatives plus top review excerpts. "
            "Pass the asin from a prior search_products result (or the session "
            "brief); pass query instead to auto-resolve the top match."
        ),
        params={
            "type": "object",
            "properties": {
                "asin": {"type": "string", "description": "Amazon ASIN, e.g. B0FQFJBBVY"},
                "query": {
                    "type": "string",
                    "description": "Product name — used ONLY when no asin is known",
                },
            },
            "required": [],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    )
)

_INSTAGRAM_TOOL = Tool(
    name="instagram_profile",
    provider="serp",
    description="Public Instagram profile lookup: followers, posts, bio. Public data only.",
    params={
        "type": "object",
        "properties": {"username": {"type": "string", "description": "Handle, with or without @"}},
        "required": ["username"],
    },
    auth_mode=AuthMode.NONE,
    source="static",
)
