"""#18 — device location auto-picks the nearest saved address (generic seam)."""

import asyncio

import pytest

from core.providers.base import ExecCtx
from core.providers.mcp.nearest import address_coords, haversine_km, pick_address_id
from core.providers.plugins.zomato import ZomatoProvider

pytestmark = pytest.mark.unit

_OFFICE_GEO = {"latitude": 12.9716, "longitude": 77.5946}  # MG Road, Bengaluru
_ADDRESSES = [
    {"address_id": "HOME", "label": "Home", "latitude": 12.9352, "longitude": 77.6245},
    {"address_id": "OFFICE", "label": "Office", "latitude": 12.9698, "longitude": 77.5997},
]


def test_pick_nearest_when_geo_known():
    assert pick_address_id(_ADDRESSES, _OFFICE_GEO) == "OFFICE"
    home_geo = {"latitude": 12.9355, "longitude": 77.6240}
    assert pick_address_id(_ADDRESSES, home_geo) == "HOME"


def test_pick_falls_back_to_first_without_geo_or_coords():
    assert pick_address_id(_ADDRESSES, None) == "HOME"
    assert pick_address_id(_ADDRESSES, {"latitude": "junk"}) == "HOME"
    bare = [{"address_id": "A1"}, {"address_id": "A2"}]
    assert pick_address_id(bare, _OFFICE_GEO) == "A1"
    assert pick_address_id([], _OFFICE_GEO) is None
    assert pick_address_id(None, _OFFICE_GEO) is None


def test_pick_skips_rows_without_usable_ids_or_coords():
    rows = [
        {"label": "no id", "latitude": 12.97, "longitude": 77.59},
        {"address_id": "FAR", "latitude": 28.6139, "longitude": 77.209},  # Delhi
        {"address_id": "NEAR", "location": {"lat": 12.9698, "lng": 77.5997}},
    ]
    assert pick_address_id(rows, _OFFICE_GEO) == "NEAR"


def test_address_coords_variant_spellings():
    assert address_coords({"lat": "12.5", "lon": "77.5"}) == (12.5, 77.5)
    assert address_coords({"geo": {"latitude": 12.5, "long": 77.5}}) == (12.5, 77.5)
    assert address_coords({"lat": 0, "lng": 0}) is None  # 0,0 sentinel = missing
    assert address_coords({"lat": 99.0, "lng": 77.5}) is None  # out of range
    assert address_coords({}) is None


def test_haversine_sanity():
    blr, delhi = (12.9716, 77.5946), (28.6139, 77.209)
    assert 1700 < haversine_km(blr, delhi) < 1800
    assert haversine_km(blr, blr) == 0.0


def test_before_run_picks_nearest_carried_address():
    ctx = ExecCtx(session={"session_addresses": list(_ADDRESSES)}, user_geo=_OFFICE_GEO)
    asyncio.run(ZomatoProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "OFFICE"


def test_before_run_without_geo_keeps_first_address_behaviour():
    ctx = ExecCtx(session={"session_addresses": list(_ADDRESSES)})
    asyncio.run(ZomatoProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "HOME"
