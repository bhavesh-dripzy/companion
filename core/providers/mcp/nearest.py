"""Nearest-saved-address selection from device location (#18).

Provider saved-address rows usually carry their own coordinates (spelled
`latitude`/`lat` + `longitude`/`lng`/`lon`, sometimes nested under
`location`/`geo`/`coordinates`). When the turn has a device or named-location
geo, the saved address nearest to it wins; rows without coordinates (or turns
without geo) fall back to the first row — the previous behaviour.
"""

from __future__ import annotations

import math
from typing import Any

_LAT_KEYS = ("latitude", "lat")
_LNG_KEYS = ("longitude", "lng", "lon", "long")
_NESTED_KEYS = ("location", "geo", "coordinates", "latlng")


def _coord(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result != 0.0 else None


def _pair(blob: dict[str, Any]) -> tuple[float, float] | None:
    lat = next((c for k in _LAT_KEYS if (c := _coord(blob.get(k))) is not None), None)
    lng = next((c for k in _LNG_KEYS if (c := _coord(blob.get(k))) is not None), None)
    if lat is None or lng is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return lat, lng


def address_coords(row: dict[str, Any]) -> tuple[float, float] | None:
    """Best-effort coordinates of one saved-address row, or None."""
    pair = _pair(row)
    if pair:
        return pair
    for key in _NESTED_KEYS:
        nested = row.get(key)
        if isinstance(nested, dict):
            pair = _pair(nested)
            if pair:
                return pair
    return None


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 12742.0 * math.asin(math.sqrt(h))


def pick_address_id(rows: Any, user_geo: Any) -> str | None:
    """The saved address nearest the user's location, else the first usable row.

    The generic seam every address-taking provider inherits via
    ``McpProvider.before_run`` — selection improves for all of them at once.
    """
    usable = [
        (row, aid)
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and (aid := str(row.get("address_id") or "").strip())
    ]
    if not usable:
        return None
    here = _pair(user_geo) if isinstance(user_geo, dict) else None
    if here:
        located = [
            (haversine_km(here, coords), aid)
            for row, aid in usable
            if (coords := address_coords(row))
        ]
        if located:
            return min(located)[1]
    return usable[0][1]
