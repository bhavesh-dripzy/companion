from __future__ import annotations

from unittest.mock import patch

from app.services.uber.client import start_uber_oauth
from app.services.uber.executor import execute_uber_cab
from app.services.uber.planner import plan_uber_cab
from app.services.uber.responder import reply_uber_cab
from companion_api.google_maps import GeoPoint


def test_uber_planner_extracts_basic_cab_booking():
    plan = plan_uber_cab(user_message="Book me an Uber Go to Delhi airport")

    assert plan["intent"] == "cab_booking"
    assert plan["action"] == "book"
    assert "airport" in plan["destination_text"].lower()
    assert plan["product_hint"] == "ubergo"


def test_uber_executor_requires_location_before_booking():
    result = execute_uber_cab(
        plan={"action": "book", "destination_text": "Delhi airport"},
        user_id="user-123",
        user_geo=None,
    )

    assert result["ok"] is False
    assert result["error_code"] == "needs_location"


def test_uber_executor_returns_deep_link_when_not_connected():
    with patch("app.services.uber.executor.geocode_address") as mock_geocode, patch(
        "app.services.uber.executor.uber_configured", return_value=False
    ), patch(
        "app.services.uber.executor.uber_status",
        return_value={
            "configured": False,
            "connected": False,
            "environment": "sandbox",
            "scopes": [],
            "can_request_rides": False,
            "production_full_access_required": False,
        },
    ):
        mock_geocode.return_value = GeoPoint(28.5562, 77.1000)
        result = execute_uber_cab(
            plan={"action": "book", "destination_text": "Delhi airport"},
            user_id="user-123",
            user_geo={"latitude": 28.4595, "longitude": 77.0266},
        )

    assert result["ok"] is False
    assert result["error_code"] == "not_connected"
    assert result["deep_link"].startswith("https://m.uber.com/ul/?")
    assert "dropoff" in result["deep_link"]


def test_uber_responder_surfaces_not_connected_link():
    text = reply_uber_cab(
        user_message="book uber",
        execute_tools=True,
        execution={
            "ok": False,
            "error_code": "not_connected",
            "deep_link": "https://m.uber.com/ul/?action=setPickup",
        },
    )

    assert "not connected" in text.lower()
    assert "https://m.uber.com" in text


def test_uber_oauth_uses_sandbox_host_by_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UBER_CLIENT_ID", "client-id")
    monkeypatch.setenv("UBER_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("UBER_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("UBER_TOKEN_STORE_FILE", str(tmp_path / "uber_tokens.json"))
    monkeypatch.delenv("UBER_AUTH_URL", raising=False)

    data = start_uber_oauth(
        user_id="local_admin",
        redirect_uri="http://127.0.0.1:8000/api/uber/oauth/callback",
    )

    assert data["authorization_url"].startswith(
        "https://sandbox-login.uber.com/oauth/v2/authorize?"
    )
