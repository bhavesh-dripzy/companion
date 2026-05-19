"""General-chat domain agent: orchestrator classified normal_query → empty tool plan."""

from __future__ import annotations

from typing import Any


def _empty_constraints() -> dict[str, Any]:
    return {
        "requires_address_resolution": False,
        "requested_location_text": None,
        "inferred_keyword": None,
        "price_max_inr": None,
        "price_min_inr": None,
        "min_rating": None,
        "near_and_fast": None,
        "vegetarian_only": None,
        "party_size_estimate": None,
        "health_constraints": None,
    }


def plan_normal_query(*, reasoning: str) -> dict[str, Any]:
    """Returns ``food_routing_plan``-shaped dict with no MCP steps."""
    return {
        "intent": "normal_query",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints(),
        "execution_notes_for_downstream": "General chat; no MCP tools.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }
