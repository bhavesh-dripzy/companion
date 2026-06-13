"""Maps routes: status + distance/ETA. Extracted from main.py (#29)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from companion_api.auth import get_current_user
from companion_api.google_maps import (
    GeoPoint,
    GoogleMapsError,
    compute_route,
    geocode_address,
    google_maps_configured,
)
from companion_api.models import MapsRouteRequest

router = APIRouter()


@router.get("/api/maps/geocode/")
def maps_geocode(q: str, _user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Geocode a free-text address/area into lat+lng. Used by the location picker."""
    if not q.strip():
        raise HTTPException(status_code=422, detail="q is required")
    point = geocode_address(q.strip(), region="in")
    if point is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return {"latitude": point.latitude, "longitude": point.longitude, "formatted": q.strip()}


@router.get("/api/maps/status/")
def maps_status(_user: str = Depends(get_current_user)) -> dict[str, bool]:
    """Used by the frontend to decide whether to surface map-aware UI."""
    return {"configured": google_maps_configured()}


@router.post("/api/maps/route")
def maps_route(
    payload: MapsRouteRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Compute distance + ETA from the user's location to a destination.

    Either ``destination_text`` *or* ``destination_point`` must be supplied.
    """
    if not payload.destination_text and not payload.destination_point:
        raise HTTPException(
            status_code=422, detail="Provide either destination_text or destination_point."
        )

    destination: Any = (
        GeoPoint(
            latitude=payload.destination_point.latitude,
            longitude=payload.destination_point.longitude,
        )
        if payload.destination_point is not None
        else payload.destination_text
    )

    try:
        route = compute_route(
            origin=GeoPoint(latitude=payload.origin.latitude, longitude=payload.origin.longitude),
            destination=destination,
            travel_mode=payload.travel_mode,
            language=payload.language,
            region=payload.region,
        )
    except GoogleMapsError as exc:
        status = (
            503
            if exc.code == "not_configured"
            else 422
            if exc.code in {"invalid_input", "no_route"}
            else 502
        )
        raise HTTPException(
            status_code=status, detail={"error_code": exc.code, "message": str(exc)}
        ) from exc

    return {"status": "success", "route": route.to_dict()}
