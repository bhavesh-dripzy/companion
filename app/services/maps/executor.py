"""Maps domain agent — execute a Routes-API call for the agent turn.

Mirrors ``app.services.calendar.executor`` shape: takes the user's message and
returns a structured envelope (``ok``, ``error``, ``error_code``,
``routed_providers``, plus a ``route`` payload on success) that
``pipeline.run_agent_turn`` then hands to the responder.
"""

from __future__ import annotations

from typing import Any

from companion_api.google_maps import (
    GeoPoint,
    GoogleMapsError,
    compute_route,
    google_maps_configured,
)


_PROVIDERS = ["google_maps"]


def _geo_from_payload(geo: Any) -> GeoPoint | None:
    """Accept ``GeoPoint`` directly or any dict with lat/lng-ish keys."""
    if isinstance(geo, GeoPoint):
        return geo
    if not isinstance(geo, dict):
        return None
    lat = geo.get("latitude", geo.get("lat"))
    lng = geo.get("longitude", geo.get("lng"))
    if lat is None or lng is None:
        return None
    try:
        return GeoPoint(float(lat), float(lng))
    except (TypeError, ValueError):
        return None


def execute_maps_query(
    *,
    plan: dict[str, Any],
    user_geo: dict[str, Any] | GeoPoint | None,
) -> dict[str, Any]:
    """Run the planner's tool_plan against the Routes API."""
    if not google_maps_configured():
        return {
            "ok": False,
            "error": "Google Maps is not configured on this server.",
            "error_code": "not_configured",
            "routed_providers": list(_PROVIDERS),
        }

    origin = _geo_from_payload(user_geo)
    if origin is None:
        return {
            "ok": False,
            "error": "I need your current location to compute distances.",
            "error_code": "needs_location",
            "routed_providers": list(_PROVIDERS),
        }

    steps = plan.get("tool_plan") if isinstance(plan, dict) else None
    first_step = steps[0] if isinstance(steps, list) and steps else {}
    args = first_step.get("arguments") if isinstance(first_step, dict) else {}
    args = args if isinstance(args, dict) else {}

    destination = str(args.get("destination") or "").strip()
    if not destination:
        return {
            "ok": False,
            "error": "I couldn't figure out the destination from your message.",
            "error_code": "missing_destination",
            "routed_providers": list(_PROVIDERS),
        }

    travel_mode = args.get("travel_mode")

    try:
        route = compute_route(
            origin=origin,
            destination=destination,
            travel_mode=travel_mode if isinstance(travel_mode, str) else None,
        )
    except GoogleMapsError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
            "routed_providers": list(_PROVIDERS),
            "destination": destination,
            "travel_mode": travel_mode,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "error_code": "api_error",
            "routed_providers": list(_PROVIDERS),
            "destination": destination,
            "travel_mode": travel_mode,
        }

    return {
        "ok": True,
        "routed_providers": list(_PROVIDERS),
        "destination": destination,
        "travel_mode": travel_mode or "DRIVE",
        "origin": {"latitude": origin.latitude, "longitude": origin.longitude},
        "route": route.to_dict(),
    }
