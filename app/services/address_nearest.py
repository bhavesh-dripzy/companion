"""Pick the saved provider address nearest to the user's current device location.

Issue #18: address selection currently falls back to "first saved address",
which is often wrong (a user at the office gets food sent home). When the
frontend supplies the device location (``user_geo``), we instead geocode each
saved address and pick the closest one — but only as a *fallback tier*, after
explicit address ids, conversation label matches, and session carryover.

Saved provider addresses carry only free-text (``address_line``), no
coordinates, so each is geocoded via Google Maps. Geocoding is cached for a day
inside ``google_maps.geocode_address``; selection uses cheap haversine on the
geocoded points (no Routes API call per address).

Everything is best-effort: missing location, no API key, or geocode failures
all yield ``None`` so the caller keeps its existing fallback.
"""

from __future__ import annotations

import logging
from math import asin, cos, radians, sin, sqrt
from typing import Any

logger = logging.getLogger(__name__)

# If the closest saved address is still absurdly far from the device fix (e.g.
# the user is travelling in another city), don't silently pick it — return None
# so the caller's existing fallback applies. Generous enough that any in-metro
# selection always wins.
_MAX_CONFIDENT_KM = 75.0


def coords_from_user_geo(user_geo: dict[str, Any] | None) -> tuple[float, float] | None:
    """Extract (lat, lng) from a user_geo dict (latitude/longitude or lat/lng)."""
    if not isinstance(user_geo, dict):
        return None
    for lat_key, lng_key in (("latitude", "longitude"), ("lat", "lng")):
        lat, lng = user_geo.get(lat_key), user_geo.get(lng_key)
        if lat is None or lng is None:
            continue
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            continue
    return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in kilometres."""
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlmb = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _address_line(addr: dict[str, Any]) -> str:
    for key in ("address_line", "addressLine", "location_name", "formatted_address"):
        val = str(addr.get(key) or "").strip()
        if val:
            return val
    return ""


def _address_id(addr: dict[str, Any]) -> str | None:
    aid = addr.get("address_id") or addr.get("addressId") or addr.get("id")
    sid = str(aid).strip() if aid is not None else ""
    return sid or None


def nearest_address_id(
    addresses: list[dict[str, Any]],
    user_geo: dict[str, Any] | None,
    *,
    region: str | None = "in",
) -> str | None:
    """Return the ``address_id`` of the saved address closest to ``user_geo``.

    Returns ``None`` when location is unavailable, geocoding is unavailable, or
    no saved address could be confidently placed near the device fix — so the
    caller can fall through to its existing selection logic.
    """
    origin = coords_from_user_geo(user_geo)
    if origin is None:
        return None
    if not isinstance(addresses, list) or not addresses:
        return None

    try:
        from companion_api.google_maps import geocode_address
    except Exception:
        logger.debug("nearest_address_id: google_maps unavailable", exc_info=True)
        return None

    olat, olng = origin
    best_id: str | None = None
    best_km = float("inf")
    geocoded_any = False

    for addr in addresses:
        if not isinstance(addr, dict):
            continue
        aid = _address_id(addr)
        if not aid:
            continue
        # Prefer coordinates already on the address (some providers include
        # them); otherwise geocode the free-text line.
        lat = addr.get("lat") if addr.get("lat") is not None else addr.get("latitude")
        lng = addr.get("lng") if addr.get("lng") is not None else addr.get("longitude")
        try:
            plat = float(lat) if lat is not None else None
            plng = float(lng) if lng is not None else None
        except (TypeError, ValueError):
            plat = plng = None

        if plat is None or plng is None:
            line = _address_line(addr)
            if not line:
                continue
            point = geocode_address(line, region=region)
            if point is None:
                continue
            plat, plng = point.latitude, point.longitude

        geocoded_any = True
        km = haversine_km(olat, olng, plat, plng)
        if km < best_km:
            best_km = km
            best_id = aid

    if not geocoded_any or best_id is None:
        return None
    if best_km > _MAX_CONFIDENT_KM:
        logger.info(
            "nearest_address_id: closest saved address is %.1f km away (> %.0f); "
            "skipping nearest-pick", best_km, _MAX_CONFIDENT_KM,
        )
        return None
    logger.info("nearest_address_id: picked %s at %.1f km from device", best_id, best_km)
    return best_id
