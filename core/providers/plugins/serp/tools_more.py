"""Batch-2 serp tool declarations: news, finance, shopping, events, place
reviews, hotel details."""

from __future__ import annotations

from core.contracts.tool import AuthMode, Tool

_DATE = {"type": "string", "description": "YYYY-MM-DD"}


def _tool(name: str, description: str, props: dict, required: list[str]) -> Tool:
    return Tool(
        name=name,
        provider="serp",
        description=description,
        params={"type": "object", "properties": props, "required": required},
        auth_mode=AuthMode.NONE,
        source="static",
    )


MORE_TOOLS: list[Tool] = [
    _tool(
        "web_search",
        "General Google web search — the CATCH-ALL lookup for any factual "
        "question that no other engine covers: who/what/when/where, definitions "
        "of niche or proper-noun things, specific stats, current real-world "
        "state. Returns a direct answer, an entity snapshot, and cited source "
        "links. Use this whenever you'd otherwise have to answer from memory and "
        "might be wrong. Read-only — fetches information, never acts.",
        {"query": {"type": "string", "description": "What to look up, in plain words"}},
        ["query"],
    ),
    _tool(
        "search_news",
        "Google News headlines (India edition): title, source, date, link per "
        "article. For current-affairs questions and daily briefings.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "get_market_data",
        "Google Finance snapshot for ONE symbol: price, day movement %, key "
        "stats, latest headlines. Symbol format 'INFY:NSE', 'RELIANCE:NSE', "
        "'NIFTY_50:INDEXNSE', 'SENSEX:INDEXBOM', 'AAPL:NASDAQ'. For general "
        "market info — NOT for the user's own portfolio/trades (that's Kite).",
        {"symbol": {"type": "string", "description": "e.g. INFY:NSE or NIFTY_50:INDEXNSE"}},
        ["symbol"],
    ),
    _tool(
        "search_shopping",
        "Google Shopping (India): one product searched across MANY stores — "
        "price per store, ratings, delivery. The cross-store price comparison "
        "tool (Amazon-only comparison is search_products).",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _tool(
        "search_events",
        "Google Events: concerts, shows, meetups, exhibitions — title, when, "
        "venue, ticket link. Name the city in location_text when given.",
        {
            "query": {"type": "string", "description": "e.g. 'comedy shows', 'concerts'"},
            "location_text": {"type": "string", "description": "City/area when the user names one"},
        },
        ["query"],
    ),
    _tool(
        "get_place_reviews",
        "ACTUAL Google Maps review texts for one place: rating, recent review "
        "snippets with authors and dates. Pass data_id from a prior "
        "search_places result (or the session brief); pass query instead to "
        "auto-resolve the top matching place.",
        {
            "data_id": {"type": "string", "description": "From search_places results"},
            "query": {"type": "string", "description": "Place name — ONLY when no data_id known"},
        },
        [],
    ),
    _tool(
        "get_hotel_details",
        "Deep dive on ONE hotel from a prior search_hotels: description, "
        "photos, amenities, location rating, nearby places, check-in/out. "
        "Needs the property_token plus the SAME query and dates as the search "
        "(all in the session brief).",
        {
            "property_token": {"type": "string"},
            "query": {"type": "string"},
            "check_in_date": _DATE,
            "check_out_date": _DATE,
        },
        ["property_token", "query", "check_in_date", "check_out_date"],
    ),
]
