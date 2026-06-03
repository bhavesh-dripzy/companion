from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    include_whatsapp_link: bool = True
    llm_optional: bool = False


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


class FoodQueryRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, str]] | None = None
    address_id: str = ""
    provider: str | None = None
    execute_tools: bool | None = None
    execute_zomato: bool | None = None
    geo: UserGeoLocation | None = None
    # Opaque carry-forward IDs the executor learned on previous turns
    # (cart_id, restaurant_id, order_id, ...). Frontends should echo back the
    # ``session_state`` value the previous turn's response returned. Keys must
    # look like ``__SOMETHING__``; non-conforming entries are silently dropped
    # by the executor.
    session_state: dict[str, str] | None = None
    # Multi-restaurant memory: every (res_id, name) pair the user has been
    # shown across this session. Lets the planner pick a known restaurant
    # without a redundant — and Zomato-fuzzy — keyword search. Frontends
    # should echo back the ``session_restaurants`` value the previous turn's
    # response returned.
    session_restaurants: list[dict[str, Any]] | None = None
    # Saved delivery addresses the executor fetched from the provider on a
    # previous turn (currently only Zomato). Frontends should echo back the
    # ``session_addresses`` value the previous turn's response returned so the
    # executor can skip the address-listing MCP call and let the user say
    # "use my home address" without another round-trip.
    session_addresses: list[dict[str, Any]] | None = None
    session_movies: list[dict[str, Any]] | None = None
    session_theatres: list[dict[str, Any]] | None = None
    # District event-surface memory — same echo contract as session_movies /
    # session_theatres. Carry-forward lists of (event_id, name), (venue_id,
    # name), (artist_id, name) tuples shown this session so the planner can
    # cite literal ids on follow-ups ("more about that comedy night") without
    # re-running discovery. ``session_reservation`` is a single dict (not LRU)
    # because District table bookings are 1-per-cart by design.
    session_events: list[dict[str, Any]] | None = None
    session_venues: list[dict[str, Any]] | None = None
    session_artists: list[dict[str, Any]] | None = None
    session_reservation: dict[str, Any] | None = None
    # Multi-item menu memory (Zomato-only today): every (res_id, variant_id,
    # item_name, addon_groups) tuple the user has been shown this session.
    # Lets the planner produce a direct create_cart for "add chicken keto
    # salad" without re-fetching the menu, and carries the addon/variant
    # schema across turns so customization picks ("with buttermilk") can be
    # resolved against the already-known leaf options. Same echo contract
    # as ``session_restaurants`` — frontends round-trip whatever the prior
    # response returned.
    session_menu: list[dict[str, Any]] | None = None
    # Current Zomato cart snapshot — ``{cart_id, res_id, restaurant_name,
    # items: [{variant_id, item_name, quantity, price, add_ons[]}], item_total,
    # final_amount}``. Replaced wholesale on the next successful ``create_cart``
    # (Zomato carts are strictly single-restaurant). Frontends echo back what
    # the prior response returned so the response LLM can report cart contents
    # on follow-up turns without re-asking Zomato.
    session_cart: dict[str, Any] | None = None
    # Single-turn deferral state: when the Swiggy executor skipped a cart
    # update because the menu item has a real variant choice (size 8"/10"/
    # 12", crust hand-tossed vs thin), the response carries the variant
    # menu under ``pending_variant_choice``. Frontends should echo it back
    # on the very next turn so the planner can resolve the user's pick
    # against literal variation ids instead of hallucinating placeholders.
    pending_variant_choice: list[dict[str, Any]] | None = None


class AgentQueryRequest(BaseModel):
    """Request body for the primary agent endpoint (POST /api/agent/query/)."""

    message: str = Field(min_length=1)
    history: list[dict[str, str]] | None = None
    address_id: str = ""
    provider: str | None = None
    execute_tools: bool | None = None
    execute_zomato: bool | None = None
    geo: UserGeoLocation | None = None
    # Multi-chat support: client echoes the chat_id it received on the
    # previous turn so all messages for this conversation land under the
    # same ``msg#<chat_id>#…`` Dynamo prefix. When omitted (or blank), the
    # backend lazy-creates a fresh chat — first-turn auto-titled from
    # ``message`` — and returns the new id in ``response.chat_id`` so the
    # client can pin it for subsequent turns.
    chat_id: str | None = None
    # See FoodQueryRequest.session_state above.
    session_state: dict[str, str] | None = None
    # See FoodQueryRequest.session_restaurants above.
    session_restaurants: list[dict[str, Any]] | None = None
    # See FoodQueryRequest.session_addresses above.
    session_addresses: list[dict[str, Any]] | None = None
    session_movies: list[dict[str, Any]] | None = None
    session_theatres: list[dict[str, Any]] | None = None
    # See FoodQueryRequest.session_events / session_venues / session_artists /
    # session_reservation above.
    session_events: list[dict[str, Any]] | None = None
    session_venues: list[dict[str, Any]] | None = None
    session_artists: list[dict[str, Any]] | None = None
    session_reservation: dict[str, Any] | None = None
    # See FoodQueryRequest.session_menu above.
    session_menu: list[dict[str, Any]] | None = None
    # See FoodQueryRequest.session_cart above.
    session_cart: dict[str, Any] | None = None
    # See FoodQueryRequest.pending_variant_choice above.
    pending_variant_choice: list[dict[str, Any]] | None = None


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
