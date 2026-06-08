"""Tests for the HTTP provider plugins (maps, calendar, uber, whatsapp)."""

import asyncio

import pytest

from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode
from core.providers.base import ExecCtx
from core.providers.plugins.calendar import CalendarProvider
from core.providers.plugins.maps import MapsProvider
from core.providers.plugins.uber import UberProvider
from core.providers.plugins.whatsapp import WhatsAppProvider
from core.registry.bootstrap import build_registry

pytestmark = pytest.mark.unit


def _run(provider, call):
    return asyncio.run(provider.run([call], ExecCtx(user_geo={"lat": 12.9, "lng": 77.6})))


def test_registry_wires_all_http_providers():
    registry = build_registry()
    for pid in ("twilio", "maps", "calendar", "uber", "whatsapp"):
        assert registry.get(pid) is not None
    assert registry.tools.get("get_directions", provider="maps") is not None
    assert registry.tools.get("book_cab", provider="uber").mutating


def test_maps_happy_path(monkeypatch):
    import companion_api.google_maps as gm

    class _Route:
        def to_dict(self):
            return {"distance_m": 5000, "duration_s": 600}

    monkeypatch.setattr(gm, "google_maps_configured", lambda: True)
    monkeypatch.setattr(gm, "compute_route", lambda **kw: _Route())
    result = _run(
        MapsProvider(), ToolCall(tool="get_directions", arguments={"destination": "Whitefield"})
    )
    assert result.ok
    assert any(a.kind == "route_card" for a in MapsProvider().build_attachments(result.results))


def test_maps_not_configured(monkeypatch):
    import companion_api.google_maps as gm

    monkeypatch.setattr(gm, "google_maps_configured", lambda: False)
    result = _run(MapsProvider(), ToolCall(tool="get_directions", arguments={"destination": "X"}))
    assert not result.ok and result.results[0].data["error_code"] == "not_configured"


def test_calendar_not_connected(monkeypatch):
    import companion_api.google_calendar as gc

    monkeypatch.setattr(gc, "google_calendar_configured", lambda: True)
    monkeypatch.setattr(gc, "google_calendar_status", lambda uid: {"connected": False})
    result = asyncio.run(
        CalendarProvider().run(
            [
                ToolCall(
                    tool="create_calendar_reminder", arguments={"reminder_text": "call mom at 6"}
                )
            ],
            ExecCtx(user_id="u1"),
        )
    )
    assert not result.ok and result.results[0].data["error_code"] == "not_connected"


def test_calendar_missing_text_is_invalid():
    result = asyncio.run(
        CalendarProvider().run([ToolCall(tool="create_calendar_reminder", arguments={})], ExecCtx())
    )
    assert result.results[0].error is ErrorCode.INVALID_ARGS


def test_uber_adapter(monkeypatch):
    import app.services.uber.executor as ube

    monkeypatch.setattr(
        ube,
        "execute_uber_cab",
        lambda **kw: {
            "ok": True,
            "deep_link": "https://m.uber.com/ul/?x=1",
            "action": kw["plan"]["action"],
        },
    )
    result = _run(
        UberProvider(), ToolCall(tool="estimate_cab", arguments={"destination_text": "Airport"})
    )
    assert result.ok and result.results[0].data["action"] == "estimate"
    assert any(a.kind == "cab_card" for a in UberProvider().build_attachments(result.results))


def test_whatsapp_adapter(monkeypatch):
    import app.services.whatsapp.client as wac

    class _Client:
        def send_text_message(self, to, text):
            return {"messages": [{"id": "wamid.1"}]}

    monkeypatch.setattr(wac, "WhatsAppClient", _Client)
    result = asyncio.run(
        WhatsAppProvider().run(
            [
                ToolCall(
                    tool="send_whatsapp_message", arguments={"to": "919876543210", "message": "hi"}
                )
            ],
            ExecCtx(),
        )
    )
    assert result.ok and result.results[0].data["messages"][0]["id"] == "wamid.1"
