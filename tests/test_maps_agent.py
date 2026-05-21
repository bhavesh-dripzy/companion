"""Tests for the maps domain agent (planner / executor / responder)."""

from __future__ import annotations

from typing import Any

import pytest

from companion_api.google_maps import GeoPoint, GoogleMapsError, RouteSummary

from app.services.maps import executor as maps_exec_mod
from app.services.maps.executor import execute_maps_query
from app.services.maps.planner import _heuristic_extract, _normalise, plan_maps_query
from app.services.maps.responder import reply_maps_query


# ---------------------------------------------------------------------------
# Planner heuristic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message, expected_destination, expected_mode",
    [
        ("how long to reach Phoenix Mall", "Phoenix Mall", None),
        ("how long will it take to reach MG Road", "MG Road", None),
        ("distance from here to Cubbon Park", "Cubbon Park", None),
        ("ETA to airport", "airport", None),
        ("drive to Indiranagar", "Indiranagar", "DRIVE"),
        ("how long to walk to the park", "the park", "WALK"),
        ("from my location to Whitefield by car", "Whitefield", "DRIVE"),
        ("time to bike to office", "office", "BICYCLE"),
    ],
)
def test_heuristic_extract_handles_common_phrasings(
    message: str, expected_destination: str, expected_mode: str | None
) -> None:
    parsed = _heuristic_extract(message)
    assert expected_destination.lower() in parsed["destination"].lower()
    assert parsed["travel_mode"] == expected_mode


def test_normalise_rejects_unknown_travel_mode() -> None:
    assert _normalise({"destination": "X", "travel_mode": "HOVER"}) == {
        "destination": "X",
        "travel_mode": None,
    }


def test_plan_maps_query_uses_heuristic_when_no_llm() -> None:
    plan = plan_maps_query(
        reasoning="maps test",
        user_message="how long to reach Phoenix Mall by car",
        llm=None,
    )
    step = plan["tool_plan"][0]
    assert step["tool"] == "compute_route"
    assert step["provider"] == "google_maps"
    assert "Phoenix Mall" in step["arguments"]["destination"]
    assert step["arguments"]["travel_mode"] == "DRIVE"
    assert plan["intent"] == "maps_query"


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _stub_configured(monkeypatch: pytest.MonkeyPatch, value: bool = True) -> None:
    monkeypatch.setattr(maps_exec_mod, "google_maps_configured", lambda: value)


def _stub_compute_route(monkeypatch: pytest.MonkeyPatch, summary: RouteSummary) -> None:
    monkeypatch.setattr(maps_exec_mod, "compute_route", lambda **kwargs: summary)


def _plan(destination: str = "Phoenix Mall", travel_mode: str | None = "DRIVE") -> dict[str, Any]:
    return {
        "tool_plan": [
            {
                "tool": "compute_route",
                "provider": "google_maps",
                "arguments": {"destination": destination, "travel_mode": travel_mode},
            }
        ]
    }


def test_executor_returns_not_configured_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_configured(monkeypatch, False)
    result = execute_maps_query(plan=_plan(), user_geo={"latitude": 12.9, "longitude": 77.6})
    assert result == {
        "ok": False,
        "error": "Google Maps is not configured on this server.",
        "error_code": "not_configured",
        "routed_providers": ["google_maps"],
    }


def test_executor_returns_needs_location_when_geo_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_configured(monkeypatch)
    result = execute_maps_query(plan=_plan(), user_geo=None)
    assert result["ok"] is False
    assert result["error_code"] == "needs_location"


def test_executor_returns_missing_destination_when_planner_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_configured(monkeypatch)
    result = execute_maps_query(
        plan=_plan(destination="", travel_mode=None),
        user_geo={"latitude": 12.9, "longitude": 77.6},
    )
    assert result["ok"] is False
    assert result["error_code"] == "missing_destination"


def test_executor_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_configured(monkeypatch)
    _stub_compute_route(
        monkeypatch,
        RouteSummary(
            distance_meters=8500,
            duration_seconds=1500,
            distance_text="8.5 km",
            duration_text="25 min",
            polyline=None,
            description="via Outer Ring Road",
        ),
    )
    result = execute_maps_query(
        plan=_plan(destination="Phoenix Mall", travel_mode="DRIVE"),
        user_geo={"latitude": 12.9, "longitude": 77.6},
    )
    assert result["ok"] is True
    assert result["destination"] == "Phoenix Mall"
    assert result["travel_mode"] == "DRIVE"
    assert result["route"]["distance_text"] == "8.5 km"
    assert result["route"]["duration_text"] == "25 min"


def test_executor_maps_invalid_input_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_configured(monkeypatch)

    def boom(**_kwargs: Any) -> RouteSummary:
        raise GoogleMapsError("bad input", code="invalid_input")

    monkeypatch.setattr(maps_exec_mod, "compute_route", boom)
    result = execute_maps_query(
        plan=_plan(),
        user_geo={"latitude": 12.9, "longitude": 77.6},
    )
    assert result["ok"] is False
    assert result["error_code"] == "invalid_input"


def test_executor_accepts_geopoint_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_configured(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_compute(**kwargs: Any) -> RouteSummary:
        captured.update(kwargs)
        return RouteSummary(100, 60, "100 m", "1 min")

    monkeypatch.setattr(maps_exec_mod, "compute_route", fake_compute)
    result = execute_maps_query(
        plan=_plan(),
        user_geo=GeoPoint(latitude=12.9, longitude=77.6),
    )
    assert result["ok"] is True
    assert isinstance(captured["origin"], GeoPoint)
    assert captured["origin"].latitude == pytest.approx(12.9)


# ---------------------------------------------------------------------------
# Responder
# ---------------------------------------------------------------------------


def test_responder_explains_needs_location() -> None:
    reply = reply_maps_query(
        user_message="how long to mg road",
        execution={"ok": False, "error_code": "needs_location", "error": "x"},
        execute_tools=True,
    )
    assert "location" in reply.lower()


def test_responder_explains_missing_destination() -> None:
    reply = reply_maps_query(
        user_message="hello",
        execution={"ok": False, "error_code": "missing_destination", "error": "x"},
        execute_tools=True,
    )
    assert "destination" in reply.lower()


def test_responder_handles_success_drive() -> None:
    reply = reply_maps_query(
        user_message="how long to phoenix",
        execution={
            "ok": True,
            "destination": "Phoenix Mall",
            "travel_mode": "DRIVE",
            "route": {
                "distance_text": "8.5 km",
                "duration_text": "25 min",
                "description": "via ORR",
            },
        },
        execute_tools=True,
    )
    assert "25 min" in reply
    assert "8.5 km" in reply
    assert "Phoenix Mall" in reply
    assert "drive" in reply.lower()


def test_responder_handles_success_walk() -> None:
    reply = reply_maps_query(
        user_message="walk to park",
        execution={
            "ok": True,
            "destination": "Cubbon Park",
            "travel_mode": "WALK",
            "route": {"distance_text": "600 m", "duration_text": "8 min"},
        },
        execute_tools=True,
    )
    assert "walk" in reply.lower()
    assert "Cubbon Park" in reply


def test_responder_respects_execute_tools_false() -> None:
    reply = reply_maps_query(
        user_message="how long to x",
        execution=None,
        execute_tools=False,
    )
    assert "tool execution" in reply.lower()
