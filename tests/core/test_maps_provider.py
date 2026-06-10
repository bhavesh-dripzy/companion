"""Unit tests for MapsProvider travel-mode normalization.

The LLM planner emits natural-language modes ("driving"); the Routes API expects
the enum ("DRIVE"). _normalize_travel_mode bridges that so a directions turn
doesn't fail with "Unsupported travel_mode".
"""

import pytest

from core.providers.plugins.maps.provider import _normalize_travel_mode

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value,expected",
    [
        ("driving", "DRIVE"),
        ("Driving", "DRIVE"),
        ("car", "DRIVE"),
        ("walking", "WALK"),
        ("on foot", "WALK"),
        ("cycling", "BICYCLE"),
        ("bike", "BICYCLE"),
        ("public transport", "TRANSIT"),
        ("metro", "TRANSIT"),
        ("scooter", "TWO_WHEELER"),
        ("DRIVE", "DRIVE"),  # already-valid enum passes through
        ("WALK", "WALK"),
    ],
)
def test_normalizes_natural_language_modes(value, expected):
    assert _normalize_travel_mode(value) == expected


@pytest.mark.parametrize("value", ["flying", "teleport", "", None, 5])
def test_unknown_or_non_string_returns_none(value):
    assert _normalize_travel_mode(value) is None


def test_resolve_origin_prefers_named_origin(monkeypatch):
    # Regression: "from Connaught Place to Cyber Hub" demanded GPS because the
    # tool had no origin parameter and always routed from current location.
    from types import SimpleNamespace

    from core.providers.plugins.maps import provider as maps_provider

    import companion_api.google_maps as gm

    monkeypatch.setattr(
        gm, "geocode_address", lambda text, region=None: SimpleNamespace(latitude=28.63, longitude=77.22)
    )
    origin = maps_provider._resolve_origin({"origin": "Connaught Place"}, None)
    assert (origin.latitude, origin.longitude) == (28.63, 77.22)


def test_resolve_origin_falls_back_to_gps_then_none(monkeypatch):
    from core.providers.plugins.maps import provider as maps_provider

    origin = maps_provider._resolve_origin({}, {"latitude": 12.9, "longitude": 77.6})
    assert (origin.latitude, origin.longitude) == (12.9, 77.6)
    assert maps_provider._resolve_origin({}, None) is None


def test_get_directions_schema_offers_origin():
    from core.providers.plugins.maps.provider import _TOOL

    assert "origin" in _TOOL.params["properties"]
    assert _TOOL.params["required"] == ["destination"]
