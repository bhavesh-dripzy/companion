"""Unit tests for the Google Maps Routes-API integration.

These tests deliberately avoid hitting the network: we monkeypatch
``requests.post`` to assert on the request shape and to feed back canned
responses.
"""

from __future__ import annotations

from typing import Any

import pytest

from companion_api import google_maps as maps_mod
from companion_api.google_maps import (
    GeoPoint,
    GoogleMapsError,
    compute_route,
    google_maps_configured,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload else "")
        self.ok = 200 <= status_code < 300

    def json(self) -> dict[str, Any]:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _stub_api_key(monkeypatch: pytest.MonkeyPatch, value: str | None = "fake-key") -> None:
    monkeypatch.setattr(maps_mod, "secret_or_env", lambda _name: value)


def test_google_maps_configured_reflects_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch, None)
    assert google_maps_configured() is False
    _stub_api_key(monkeypatch, "abc")
    assert google_maps_configured() is True


def test_compute_route_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch, None)
    with pytest.raises(GoogleMapsError) as excinfo:
        compute_route(
            origin=GeoPoint(12.9, 77.5),
            destination="Phoenix Mall, Bangalore",
        )
    assert excinfo.value.code == "not_configured"


def test_compute_route_builds_well_formed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: int) -> _FakeResponse:
        captured.update({"url": url, "headers": headers, "body": json, "timeout": timeout})
        return _FakeResponse(
            200,
            {
                "routes": [
                    {
                        "distanceMeters": 12340,
                        "duration": "2700s",
                        "polyline": {"encodedPolyline": "abc123"},
                        "description": "via Outer Ring Road",
                    }
                ]
            },
        )

    monkeypatch.setattr(maps_mod.requests, "post", fake_post)

    result = compute_route(
        origin=GeoPoint(12.9352, 77.6245),
        destination="Phoenix Mall, Bangalore",
        travel_mode="drive",
    )

    assert result.distance_meters == 12340
    assert result.duration_seconds == 2700
    assert result.distance_text == "12.3 km"
    assert result.duration_text == "45 min"
    assert result.polyline == "abc123"
    assert result.description == "via Outer Ring Road"

    body = captured["body"]
    assert body["travelMode"] == "DRIVE"
    assert body["routingPreference"] == "TRAFFIC_AWARE"
    assert body["destination"] == {"address": "Phoenix Mall, Bangalore"}
    assert body["origin"]["location"]["latLng"]["latitude"] == 12.9352
    assert body["units"] == "METRIC"
    assert captured["headers"]["X-Goog-Api-Key"] == "fake-key"
    assert "routes.duration" in captured["headers"]["X-Goog-FieldMask"]


def test_compute_route_accepts_coordinate_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_post(url, headers, json, timeout):  # type: ignore[no-untyped-def]
        captured["body"] = json
        return _FakeResponse(
            200,
            {"routes": [{"distanceMeters": 500, "duration": "75s"}]},
        )

    monkeypatch.setattr(maps_mod.requests, "post", fake_post)

    result = compute_route(
        origin=GeoPoint(12.9, 77.5),
        destination=GeoPoint(13.0, 77.6),
        travel_mode="WALK",
    )

    assert captured["body"]["destination"]["location"]["latLng"]["latitude"] == 13.0
    assert captured["body"]["travelMode"] == "WALK"
    assert "routingPreference" not in captured["body"]
    assert result.distance_text == "500 m"
    assert result.duration_text == "1 min"


def test_compute_route_rejects_unsupported_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch)
    with pytest.raises(GoogleMapsError) as excinfo:
        compute_route(
            origin=GeoPoint(0, 0),
            destination="anywhere",
            travel_mode="HOVERCRAFT",
        )
    assert excinfo.value.code == "invalid_input"


def test_compute_route_rejects_empty_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch)
    with pytest.raises(GoogleMapsError) as excinfo:
        compute_route(origin=GeoPoint(0, 0), destination="   ")
    assert excinfo.value.code == "invalid_input"


def test_compute_route_no_routes_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch)
    monkeypatch.setattr(
        maps_mod.requests,
        "post",
        lambda *a, **k: _FakeResponse(200, {"routes": []}),
    )
    with pytest.raises(GoogleMapsError) as excinfo:
        compute_route(origin=GeoPoint(0, 0), destination="x")
    assert excinfo.value.code == "no_route"


def test_compute_route_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_api_key(monkeypatch)
    monkeypatch.setattr(
        maps_mod.requests,
        "post",
        lambda *a, **k: _FakeResponse(403, text="API key not authorised"),
    )
    with pytest.raises(GoogleMapsError) as excinfo:
        compute_route(origin=GeoPoint(0, 0), destination="x")
    assert excinfo.value.code == "upstream_error"
