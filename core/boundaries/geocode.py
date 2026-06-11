"""Geocoding adapter — the Google Maps client behind the pipeline seam.

The pipeline only knows `GeocodeService`; the Google client (with its day-long
result cache) lives in `companion_api.google_maps`.
"""

from __future__ import annotations

import asyncio


class GoogleGeocodeService:
    async def geocode(self, text: str) -> tuple[float, float] | None:
        from companion_api.google_maps import geocode_address

        point = await asyncio.to_thread(geocode_address, text, region="in")
        return (point.latitude, point.longitude) if point is not None else None
