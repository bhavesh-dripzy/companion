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
    # None lets the Routes API default (DRIVE) apply rather than erroring.
    assert _normalize_travel_mode(value) is None
