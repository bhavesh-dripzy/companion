"""Calendar/reminder domain agent: orchestrator classified reminder_calendar → tool plan."""

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


def plan_reminder_calendar(
    *,
    reasoning: str,
    user_message: str,
) -> dict[str, Any]:
    """Returns ``food_routing_plan``-shaped dict; execution uses Google Calendar APIs."""
    return {
        "intent": "reminder_calendar",
        "summary": reasoning,
        "tool_plan": [
            {
                "tool": "create_google_calendar_event",
                "provider": "google_calendar",
                "arguments": {"message": user_message.strip()},
            }
        ],
        "constraints": _empty_constraints(),
        "execution_notes_for_downstream": (
            "Parse natural-language time, then create a Google Calendar event with reminders."
        ),
        "validation": {
            "tool_count": 1,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "google_calendar",
        },
    }
