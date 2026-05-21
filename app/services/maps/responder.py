"""Maps domain agent: execution result → user-facing reply.

Same pattern as ``app.services.calendar.responder``: deterministic template
strings (no extra LLM round-trip) keyed off ``error_code`` or success.
"""

from __future__ import annotations

from typing import Any


_TRAVEL_MODE_VERBS: dict[str, str] = {
    "DRIVE": "drive",
    "WALK": "walk",
    "BICYCLE": "cycle",
    "TWO_WHEELER": "ride",
    "TRANSIT": "travel by transit",
}


def reply_maps_query(
    *,
    user_message: str,
    execution: dict[str, Any] | None,
    execute_tools: bool,
) -> str:
    if not execute_tools:
        return (
            "I can check the distance and ETA for you — turn on tool execution "
            "and ask again."
        )

    if not isinstance(execution, dict):
        return "I couldn't process that maps request. Please try again."

    if execution.get("ok"):
        route = execution.get("route") if isinstance(execution.get("route"), dict) else {}
        destination = str(execution.get("destination") or "your destination").strip()
        mode = str(execution.get("travel_mode") or "DRIVE").upper()
        verb = _TRAVEL_MODE_VERBS.get(mode, "go")
        distance_text = str(route.get("distance_text") or "").strip()
        duration_text = str(route.get("duration_text") or "").strip()

        if distance_text and duration_text:
            base = f"It'll take about {duration_text} to {verb} to {destination} — {distance_text} from your current location."
        elif duration_text:
            base = f"It'll take about {duration_text} to {verb} to {destination} from where you are."
        elif distance_text:
            base = f"{destination} is about {distance_text} from your current location."
        else:
            base = f"I found a route to {destination}, but no distance/ETA was returned."

        description = str(route.get("description") or "").strip()
        if description:
            base += f" ({description})"
        return base

    code = str(execution.get("error_code") or "").strip()
    err = str(execution.get("error") or "Maps request failed.").strip()

    if code == "needs_location":
        return (
            "I need your current location to figure that out. Please allow "
            "location access in your browser and ask me again."
        )
    if code == "not_configured":
        return (
            "Google Maps isn't configured on this server yet. "
            "Ask an admin to add the GOOGLE_MAPS_API_KEY secret."
        )
    if code == "missing_destination":
        return (
            "I couldn't pick out a destination from that. Try: "
            "'how long to reach Phoenix Mall, Bangalore' or 'distance from here to MG Road'."
        )
    if code == "no_route":
        destination = str(execution.get("destination") or "that destination").strip()
        return f"I couldn't find a route to {destination}. Could you try a different address?"
    if code == "invalid_input":
        return err
    return err
