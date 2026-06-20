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


PREAMBLE = (
    "You are the information agent (SerpApi-backed; read-only, no booking or "
    "purchasing). Decide whether the user's message needs a live lookup, and if so "
    "translate it into a machine-ready tool plan.\n"
    "Respond with ONLY a JSON object shaped exactly like:\n"
    '{"intent": "<snake_case>", "summary": "<one sentence>", '
    '"tool_plan": [{"tool": "<exact tool name>", "arguments": {<filled from the user\'s words>}}], '
    '"constraints": {"requires_address_resolution": false, '
    '"requested_location_text": "<city/locality the user named, else null>"}}\n'
    "Fill EVERY required argument (marked * in the tool list) with literal values.\n"
    "- WHEN TO FETCH vs. ANSWER DIRECTLY: if the message wants information that is "
    "current, external, or specific enough that you cannot answer it reliably from "
    "your own memory (news, prices, scores, facts about real people/places/products, "
    "anything 'latest/today/now'), pick a tool and fetch — in ANY language. Only "
    "return an EMPTY tool_plan ([]) for pure greetings, chit-chat, thanks, or an "
    "opinion / general-reasoning question you can answer well from your own knowledge "
    "alone. When unsure, FETCH — never refuse or claim you lack live access.\n"
    "- web_search is the DEFAULT catch-all for any factual question no more specific "
    "engine covers; search_news for news / current affairs / 'what's happening'. "
    "Definitions, who/what/when/where, niche or proper-noun facts, specific stats, "
    "the current state of a real-world topic all belong on a tool, not your memory.\n"
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
