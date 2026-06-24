from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str


class UserGeoLocation(BaseModel):
    """User's current geographic location, captured by the frontend.

    Optional on every agent request — if the user hasn't granted geolocation
    permission, the field is simply omitted and the agent will ask for it
    when it actually needs it (e.g. maps_query).
    """

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    accuracy_meters: float | None = Field(default=None, ge=0)
    captured_at: str | None = None


# NOTE: the per-turn `session_*` context protocol (cart/restaurants/menu/
# addresses/zepto/instamart/kite/serp/movies/…), the committed `selected_*`
# address selections, `pending_variant_choice`, and the old planner knobs
# (`provider`, `execute_zomato`) were removed with the legacy orchestrator.
# The new backend will define its own request/context contract from scratch.


class FoodQueryRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, str]] | None = None
    address_id: str = ""
    execute_tools: bool | None = None
    geo: UserGeoLocation | None = None


class AgentQueryRequest(BaseModel):
    """Request body for the primary agent endpoint (POST /api/agent/query/)."""

    message: str = Field(min_length=1)
    history: list[dict[str, str]] | None = None
    address_id: str = ""
    execute_tools: bool | None = None
    geo: UserGeoLocation | None = None
    # Multi-chat support: client echoes the chat_id it received on the
    # previous turn so all messages for this conversation land under the
    # same ``msg#<chat_id>#…`` Dynamo prefix. When omitted (or blank), the
    # backend lazy-creates a fresh chat — first-turn auto-titled from
    # ``message`` — and returns the new id in ``response.chat_id`` so the
    # client can pin it for subsequent turns.
    chat_id: str | None = None


class CustomMcpProviderRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    mcp_url: str = Field(min_length=8, max_length=500)
    tools: list[str] | None = None


class ZapierMcpProviderRequest(BaseModel):
    mcp_url: str = Field(min_length=8, max_length=500)
    tools: list[str] | None = None


class ReminderCreateRequest(BaseModel):
    member_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    type: str = "custom"
    schedule_type: Literal["fixed", "interval"] = "interval"
    next_trigger: datetime
    interval_minutes: int | None = None
    retry_interval_minutes: int = 5
    requires_confirmation: bool = True
    created_by: str | None = None


class GoogleCalendarReminderRequest(BaseModel):
    message: str = Field(min_length=1)
    calendar_id: str = "primary"
    timezone: str | None = None
    email_reminder_minutes: int = Field(default=0, ge=0, le=40320)
    popup_reminder_minutes: int | None = Field(default=10, ge=0, le=40320)
    store_reminder: bool = True


class ReminderResponseRequest(BaseModel):
    reminder_id: str = Field(min_length=1)
    member_id: str = Field(min_length=1)
    response: Literal["yes", "no"]


class ReminderEvent(BaseModel):
    member_id: str
    reminder_id: str


class MapsRoutePoint(BaseModel):
    """A geographic coordinate (WGS-84). Latitude in [-90, 90], longitude in [-180, 180]."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


class MapsRouteRequest(BaseModel):
    """Request body for POST /api/maps/route.

    ``origin`` is the user's current location (from ``navigator.geolocation``
    in the browser). ``destination`` may be a coordinate pair *or* a free-text
    address — the Routes API geocodes addresses for us.
    """

    origin: MapsRoutePoint
    destination_text: str | None = Field(default=None, max_length=300)
    destination_point: MapsRoutePoint | None = None
    travel_mode: Literal["DRIVE", "WALK", "BICYCLE", "TWO_WHEELER", "TRANSIT"] | None = None
    language: str | None = Field(default=None, max_length=16)
    region: str | None = Field(default=None, max_length=8)


JsonDict = dict[str, Any]
