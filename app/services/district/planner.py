"""District domain agent: user message → JSON ``tool_plan`` for MCP."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.food_llm_router import complete_json_object, finalize_tool_plan
from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"

_DISTRICT_TOOLS = frozenset(
    {
        # Dining (table reservations)
        "search_dining_restaurants",
        "get_dynamic_filters",
        "get_dining_restaurants_details",
        "get_table_reservation_slots",
        "create_table_reservation_booking",
        "get_booking_details",
        # Movies
        "get_dynamic_filters_movie",
        "search_movies",
        "search_theatres",
        "search_shows",
        "get_movie_critique_reviews",
        # Events / venues / artists
        "get_dynamic_filters_event",
        "search_events",
        "search_event_venues",
        "search_artists",
    }
)


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return (_PROMPTS / "going_out.system.md").read_text(encoding="utf-8").strip()


def plan_district(
    *,
    user_message: str,
    llm: ResolvedLlm,
) -> dict[str, Any]:
    data = complete_json_object(
        user_message=user_message,
        system_prompt=_load_prompt(),
        api_key=llm.api_key,
        model=llm.model,
        provider=llm.provider,  # type: ignore[arg-type]
        base_url=llm.base_url,
    )
    return finalize_tool_plan(
        data,
        provider=llm.provider,  # type: ignore[arg-type]
        model=llm.model,
        trace_id="",
        allowed_tools=set(_DISTRICT_TOOLS),
    )
