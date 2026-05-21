"""Google Maps Routes API integration.

Single capability for the first iteration: compute the route (distance + ETA)
from the user's current location to a destination, using the **Routes API**
(``directions/v2:computeRoutes``). This is Google's recommended replacement for
the legacy Directions / Distance Matrix APIs.

Design choices (kept intentionally small):

- **No hardcoded secrets**. The API key is read via
  :func:`companion_api.secrets.secret_or_env` (env var first, then the AWS
  Secrets Manager backed JSON secret). Same lookup model as Google Calendar.
- **No hardcoded URLs / modes / timeouts / language / region**. All overridable
  via env vars (see ``companion_api.settings``).
- **Field mask is mandatory** for the Routes API — sending an unconstrained
  request would be billed at the most expensive SKU. We always pin it.
- **Inputs are normalised** through small dataclasses so callers don't have to
  know Routes-API request shape.
- **Errors carry structured codes** (``not_configured``, ``invalid_input``,
  ``upstream_error``, ``no_route``) so the agent can branch on them without
  string-matching messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from companion_api import settings
from companion_api.secrets import secret_or_env

logger = logging.getLogger(__name__)


_DEFAULT_FIELD_MASK = (
    "routes.duration,"
    "routes.distanceMeters,"
    "routes.polyline.encodedPolyline,"
    "routes.description"
)

_VALID_TRAVEL_MODES = frozenset({"DRIVE", "WALK", "BICYCLE", "TWO_WHEELER", "TRANSIT"})


class GoogleMapsError(RuntimeError):
    """Raised for any Routes-API failure that the caller should surface.

    The ``code`` attribute lets agents/UI distinguish:

    - ``not_configured`` — server has no API key
    - ``invalid_input``  — caller-side input issue (bad coords, missing dest)
    - ``upstream_error`` — non-2xx from Google
    - ``no_route``       — Google returned 200 but no routes
    """

    def __init__(self, message: str, *, code: str = "upstream_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GeoPoint:
    """A WGS-84 coordinate. Keeps the API surface ergonomic for callers."""

    latitude: float
    longitude: float

    def to_routes_origin(self) -> dict[str, Any]:
        return {"location": {"latLng": {"latitude": self.latitude, "longitude": self.longitude}}}


@dataclass(frozen=True)
class RouteSummary:
    """Compact, UI-friendly representation of a single computed route."""

    distance_meters: int
    duration_seconds: int
    distance_text: str
    duration_text: str
    polyline: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "distance_meters": self.distance_meters,
            "duration_seconds": self.duration_seconds,
            "distance_text": self.distance_text,
            "duration_text": self.duration_text,
            "polyline": self.polyline,
            "description": self.description,
        }


def _api_key() -> str:
    key = secret_or_env("GOOGLE_MAPS_API_KEY")
    if not key:
        raise GoogleMapsError(
            "Google Maps API key is not configured on this server.",
            code="not_configured",
        )
    return key


def google_maps_configured() -> bool:
    """Cheap pre-check used by the agent / health endpoint."""
    return bool(secret_or_env("GOOGLE_MAPS_API_KEY"))


def _normalise_travel_mode(travel_mode: str | None) -> str:
    mode = (travel_mode or settings.GOOGLE_MAPS_DEFAULT_TRAVEL_MODE or "DRIVE").strip().upper()
    if mode not in _VALID_TRAVEL_MODES:
        raise GoogleMapsError(
            f"Unsupported travel_mode '{travel_mode}'. "
            f"Allowed: {sorted(_VALID_TRAVEL_MODES)}.",
            code="invalid_input",
        )
    return mode


def _normalise_destination(destination: dict[str, Any] | GeoPoint | str | None) -> dict[str, Any]:
    """Accept a coordinate, a free-text address, or a structured dict."""
    if destination is None:
        raise GoogleMapsError("Destination is required.", code="invalid_input")
    if isinstance(destination, GeoPoint):
        return destination.to_routes_origin()
    if isinstance(destination, str):
        text = destination.strip()
        if not text:
            raise GoogleMapsError("Destination address is empty.", code="invalid_input")
        return {"address": text}
    if isinstance(destination, dict):
        lat = destination.get("latitude") if "latitude" in destination else destination.get("lat")
        lng = destination.get("longitude") if "longitude" in destination else destination.get("lng")
        if lat is not None and lng is not None:
            return GeoPoint(float(lat), float(lng)).to_routes_origin()
        address = destination.get("address") or destination.get("text")
        if address:
            return {"address": str(address).strip()}
    raise GoogleMapsError(
        "Destination must be a {latitude,longitude} pair, an address string, "
        "or an object with 'address'/'text'.",
        code="invalid_input",
    )


def _humanize_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, rem_min = divmod(minutes, 60)
    if rem_min == 0:
        return f"{hours} hr"
    return f"{hours} hr {rem_min} min"


def _humanize_distance(meters: int) -> str:
    meters = max(0, int(meters))
    if meters < 1000:
        return f"{meters} m"
    km = meters / 1000.0
    if km < 100:
        return f"{km:.1f} km"
    return f"{int(round(km))} km"


def _parse_routes_duration(raw: Any) -> int:
    """Routes API returns ``duration`` as a string like ``"1234s"``."""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return int(float(text))
    except ValueError:
        return 0


def compute_route(
    *,
    origin: GeoPoint | dict[str, Any],
    destination: GeoPoint | dict[str, Any] | str,
    travel_mode: str | None = None,
    language: str | None = None,
    region: str | None = None,
) -> RouteSummary:
    """Compute a single route from ``origin`` to ``destination``.

    Parameters
    ----------
    origin:
        The user's *current location* as a :class:`GeoPoint` or a dict with
        ``latitude``/``longitude`` (or ``lat``/``lng``).
    destination:
        Coordinates, a free-text address, or a dict with ``address``/``text``
        or ``latitude``/``longitude``.
    travel_mode:
        One of ``DRIVE`` (default), ``WALK``, ``BICYCLE``, ``TWO_WHEELER``,
        ``TRANSIT``. Falls back to the server default when ``None``.
    language / region:
        Optional locale hints for human-readable strings. Defaults come from
        ``settings.GOOGLE_MAPS_DEFAULT_LANGUAGE`` / ``..._REGION``.

    Raises
    ------
    GoogleMapsError
        With one of the documented ``code`` values.
    """
    api_key = _api_key()
    origin_point = origin if isinstance(origin, GeoPoint) else GeoPoint(
        float((origin.get("latitude") if "latitude" in origin else origin.get("lat"))),
        float((origin.get("longitude") if "longitude" in origin else origin.get("lng"))),
    )

    body: dict[str, Any] = {
        "origin": origin_point.to_routes_origin(),
        "destination": _normalise_destination(destination),
        "travelMode": _normalise_travel_mode(travel_mode),
        "computeAlternativeRoutes": False,
        "languageCode": (language or settings.GOOGLE_MAPS_DEFAULT_LANGUAGE),
        "regionCode": (region or settings.GOOGLE_MAPS_DEFAULT_REGION),
        "units": "METRIC",
    }
    if body["travelMode"] == "DRIVE":
        body["routingPreference"] = "TRAFFIC_AWARE"

    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": _DEFAULT_FIELD_MASK,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            settings.GOOGLE_MAPS_ROUTES_URL,
            headers=headers,
            json=body,
            timeout=settings.GOOGLE_MAPS_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.exception("Routes API request failed")
        raise GoogleMapsError(
            f"Network error talking to Routes API: {exc}",
            code="upstream_error",
        ) from exc

    if not response.ok:
        snippet = response.text[:300].replace("\n", " ")
        logger.warning("Routes API non-2xx: %s %s", response.status_code, snippet)
        raise GoogleMapsError(
            f"Routes API returned HTTP {response.status_code}: {snippet}",
            code="upstream_error",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleMapsError("Routes API returned non-JSON.", code="upstream_error") from exc

    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not isinstance(routes, list) or not routes:
        raise GoogleMapsError(
            "No route was returned for the given origin/destination.",
            code="no_route",
        )

    first = routes[0] if isinstance(routes[0], dict) else {}
    distance_m = int(first.get("distanceMeters") or 0)
    duration_s = _parse_routes_duration(first.get("duration"))
    polyline = None
    polyline_block = first.get("polyline")
    if isinstance(polyline_block, dict):
        polyline = polyline_block.get("encodedPolyline")
    description = first.get("description")

    return RouteSummary(
        distance_meters=distance_m,
        duration_seconds=duration_s,
        distance_text=_humanize_distance(distance_m),
        duration_text=_humanize_duration(duration_s),
        polyline=str(polyline) if polyline else None,
        description=str(description) if description else None,
    )
