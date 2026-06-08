"""District MCP tool execution via mcp-remote + stdio.

Planner-first architecture (mirrors ``zomato/mcp_executor.py`` and
``swiggy/mcp_executor.py``):

* The **planner LLM** is fully responsible for every literal argument —
  filter shapes (dining vs movies vs events use different keys), booking
  date / party size, integer ``movie_id``, ``filter_values`` lists, etc.
* The **executor** does only deterministic plumbing:
  - stdio bridge to ``mcp-remote`` with the user's OAuth tokens
  - placeholder substitution (``__USER_LAT__``, ``__USER_LNG__``,
    ``__RES_ID__``, ``__BOOKING_ID__``, ``__MOVIE_ID__``,
    ``__THEATRE_ID__``, ``__SHOW_ID__``, ``__EVENT_ID__``,
    ``__VENUE_ID__``, ``__ARTIST_ID__``)
  - post-call harvesting of placeholders + session indexes (restaurants,
    movies, theatres, events, venues, artists, reservation snapshot)
  - one narrow defensive coercion: ``latitude`` / ``longitude`` strings
    coming out of placeholder substitution are converted back to floats,
    since the MCP server rejects string-typed coords.

State carried across turns mirrors the Zomato / Swiggy contract:
``session_placeholders``, ``session_restaurants``, ``session_movies``,
``session_theatres``, ``session_events``, ``session_venues``,
``session_artists``, and ``session_reservation``.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("DISTRICT_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("DISTRICT_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "45")
)
_DEFAULT_DISTRICT_MCP_URL = "https://mcp-server.district.in/mcp"
_MAX_REMEMBERED = max(1, int(os.getenv("DISTRICT_REMEMBERED_CAP", "8")))

from app.services.zomato.mcp_executor import (  # noqa: E402
    _humanize_mcp_failure,
    _merge_recent_restaurants,
    _stdio_env,
    _tool_result_payload,
    _walk_json,
    ensure_mcp_tokens_on_disk,
    repo_root_from_settings,
    substitute_placeholders,
)

# Placeholder → field-name aliases. After every tool call the executor
# walks the response, and the first dict containing one of these aliases
# wins for the corresponding placeholder. Keeps multi-turn flows working
# without the planner having to thread ids itself.
_DISTRICT_PLACEHOLDER_ALIASES: dict[str, tuple[str, ...]] = {
    "__RES_ID__": ("res_id", "restaurant_id", "restaurantId"),
    "__BOOKING_ID__": ("booking_id", "order_id", "orderId"),
    "__MOVIE_ID__": ("movie_id", "movieId", "content_id", "contentId"),
    "__THEATRE_ID__": ("theatre_id", "theater_id", "theatreId", "cinema_id"),
    "__SHOW_ID__": ("show_id", "showId"),
    "__EVENT_ID__": ("event_id", "eventId"),
    "__VENUE_ID__": ("venue_id", "venueId"),
    "__ARTIST_ID__": ("artist_id", "artistId"),
}

# These placeholders persist across turns (the response LLM can refer to
# them as "the restaurant we discussed" etc.). The location pair is
# included so subsequent searches keep using the same user-anchored
# coords without having to re-geocode every turn.
_DISTRICT_SESSION_PERSISTENT = frozenset(
    {
        "__USER_LAT__",
        "__USER_LNG__",
        "__RES_ID__",
        "__BOOKING_ID__",
        "__MOVIE_ID__",
        "__THEATRE_ID__",
        "__SHOW_ID__",
        "__EVENT_ID__",
        "__VENUE_ID__",
        "__ARTIST_ID__",
    }
)

# Tools that *issue* a fresh value for a placeholder. When a turn re-runs
# one of these, any value seeded from a previous turn is stale and must be
# cleared so the new response's value is re-learned. Same pattern as
# Zomato's ``_TOOL_ISSUES_PLACEHOLDER``.
_DISTRICT_TOOL_ISSUES_PLACEHOLDER: dict[str, tuple[str, ...]] = {
    "search_dining_restaurants": ("__RES_ID__",),
    "search_movies": ("__MOVIE_ID__",),
    "search_theatres": ("__THEATRE_ID__",),
    "search_shows": ("__SHOW_ID__",),
    "search_events": ("__EVENT_ID__",),
    "search_event_venues": ("__VENUE_ID__",),
    "search_artists": ("__ARTIST_ID__",),
    "create_table_reservation_booking": ("__BOOKING_ID__",),
}


# ---------------------------------------------------------------------------
# Defensive scrub: placeholder substitution returns strings, but the MCP
# server rejects ``user_location: {latitude: "12.93", longitude: "77.62"}``
# — it wants numbers. So after substitution, walk the args and convert any
# numeric-looking ``latitude`` / ``longitude`` strings back to floats.
# This is analogous to ``_scrub_cart_items_for_zomato`` — narrow, justified,
# and not a transfer of LLM responsibility (the planner just emits the
# placeholder string, identical to how ``__ADDRESS_ID__`` works for Zomato).
# ---------------------------------------------------------------------------
_LATLNG_KEYS = frozenset({"latitude", "longitude", "lat", "lng", "lon"})


def _coerce_latlng_floats(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _LATLNG_KEYS and isinstance(v, str):
                try:
                    out[k] = float(v.strip())
                    continue
                except (TypeError, ValueError):
                    pass
            out[k] = _coerce_latlng_floats(v)
        return out
    if isinstance(obj, list):
        return [_coerce_latlng_floats(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------
def _parse_envelope_data(envelope: dict[str, Any]) -> Any:
    data = envelope.get("data")
    if isinstance(data, str):
        try:
            import json

            return json.loads(data)
        except Exception:
            return data
    return data


def _district_is_error(envelope: dict[str, Any]) -> bool:
    if envelope.get("is_error"):
        return True
    blob = _parse_envelope_data(envelope)
    if isinstance(blob, dict):
        status = blob.get("status")
        if isinstance(status, dict) and str(status.get("status") or "").lower() == "error":
            return True
    return False


def _learn_district_placeholders(values: dict[str, str], envelope: dict[str, Any]) -> None:
    """Walk the response once and grab the first match for each known alias.

    First-wins — same semantics as Zomato's ``_learn_placeholders_from_payload``.
    The caller is responsible for clearing placeholders this tool issues
    (see ``_DISTRICT_TOOL_ISSUES_PLACEHOLDER``).
    """
    blob = _parse_envelope_data(envelope)
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        for placeholder, aliases in _DISTRICT_PLACEHOLDER_ALIASES.items():
            if placeholder in values:
                continue
            for alias in aliases:
                val = obj.get(alias)
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    values[placeholder] = text
                    break
        # `create_table_reservation_booking` nests the booking under `result.booking_id`.
        result = obj.get("result")
        if isinstance(result, dict):
            bid = result.get("booking_id") or result.get("order_id")
            if bid and "__BOOKING_ID__" not in values:
                values["__BOOKING_ID__"] = str(bid).strip()


# ---------------------------------------------------------------------------
# Session-index collectors (one per discovery surface). Each one walks the
# envelope, normalises the row shape, and returns a list ready for LRU
# merging at the end of the turn.
# ---------------------------------------------------------------------------
def _first_list_field(envelope: dict[str, Any], field: str) -> list[dict[str, Any]]:
    blob = _parse_envelope_data(envelope)
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        raw = obj.get(field)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    return []


def _collect_restaurants(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _first_list_field(envelope, "restaurants"):
        rid = item.get("res_id") or item.get("restaurant_id") or item.get("id")
        name = item.get("res_name") or item.get("name") or item.get("restaurant_name")
        if rid is None or not name:
            continue
        rows.append(
            {
                "res_id": str(rid).strip(),
                "name": str(name).strip(),
                "rating": item.get("rating") or item.get("avg_rating"),
                "cuisine": item.get("cuisine") or item.get("cuisines"),
                "locality": item.get("locality") or item.get("area"),
                "image_url": item.get("image_url") or item.get("photo_url"),
                "price_for_two": item.get("price_for_two") or item.get("cost_for_two"),
                "bookable_via_agent": item.get("bookable_via_agent"),
                "distance": item.get("distance"),
                "web_url": item.get("web_url"),
            }
        )
    return rows


def _collect_movies(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _first_list_field(envelope, "movies"):
        mid = item.get("movie_id") or item.get("id") or item.get("content_id")
        name = item.get("movie_name") or item.get("name") or item.get("title")
        if mid is None or not name:
            continue
        rows.append(
            {
                "movie_id": str(mid).strip(),
                "name": str(name).strip(),
                "language": item.get("language") or item.get("languages"),
                "genre": item.get("genre") or item.get("genres"),
                "duration_minutes": item.get("duration_minutes") or item.get("duration"),
                "rating": item.get("rating") or item.get("imdb_rating"),
                "release_date": item.get("release_date"),
                "cbfc_certification": item.get("cbfc_certification"),
                "poster_url": (
                    item.get("poster_url")
                    or item.get("image_url")
                    or item.get("spotlight_thumbnail_url")
                ),
                "spotlight_video_url": item.get("spotlight_video_url"),
                "web_url": item.get("web_url"),
            }
        )
    return rows


def _collect_theatres(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _first_list_field(envelope, "theatres"):
        tid = (
            item.get("theatre_id")
            or item.get("theater_id")
            or item.get("id")
            or item.get("cinema_id")
        )
        name = item.get("theatre_name") or item.get("name") or item.get("theater_name")
        if tid is None or not name:
            continue
        rows.append(
            {
                "theatre_id": str(tid).strip(),
                "name": str(name).strip(),
                "locality": item.get("locality") or item.get("area"),
                "distance": item.get("distance"),
                "image_url": item.get("image_url") or item.get("photo_url"),
                "web_url": item.get("web_url"),
            }
        )
    return rows


def _collect_events(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _first_list_field(envelope, "events"):
        eid = item.get("event_id") or item.get("id")
        name = item.get("name") or item.get("event_name") or item.get("title")
        if eid is None or not name:
            continue
        rows.append(
            {
                "event_id": str(eid).strip(),
                "name": str(name).strip(),
                "date_string": item.get("date_string") or item.get("date"),
                "venue_name": item.get("venue_name"),
                "locality": item.get("locality"),
                "city": item.get("city"),
                "price_string": item.get("price_string"),
                "min_price": item.get("min_price"),
                "image_url": item.get("image_url"),
                "video_url": item.get("video_url"),
                "web_url": item.get("web_url"),
                "is_dining_event": item.get("is_dining_event"),
                "is_activity": item.get("is_activity"),
                "is_play": item.get("is_play"),
                "is_attraction": item.get("is_attraction"),
            }
        )
    return rows


def _collect_venues(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _first_list_field(envelope, "venues"):
        vid = item.get("venue_id") or item.get("id")
        name = item.get("venue_name") or item.get("name")
        if vid is None or not name:
            continue
        rows.append(
            {
                "venue_id": str(vid).strip(),
                "name": str(name).strip(),
                "city": item.get("city"),
                "locality": item.get("locality"),
                "distance": item.get("distance"),
                "image_url": item.get("image_url"),
                "web_url": item.get("web_url"),
                "event_names": item.get("event_names"),
                "categories": item.get("categories"),
            }
        )
    return rows


def _collect_artists(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _first_list_field(envelope, "artists"):
        aid = item.get("artist_id") or item.get("id")
        name = item.get("artist_name") or item.get("name")
        if aid is None or not name:
            continue
        rows.append(
            {
                "artist_id": str(aid).strip(),
                "name": str(name).strip(),
                "artist_type": item.get("artist_type"),
                "genres": item.get("genres"),
                "description": item.get("description"),
                "image_url": item.get("image_url"),
                "upcoming_event_count": item.get("upcoming_event_count"),
            }
        )
    return rows


def _extract_reservation_snapshot(
    envelope: dict[str, Any],
    *,
    fallback_args: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Lift a ``create_table_reservation_booking`` / ``get_booking_details``
    envelope into a chat-renderable reservation snapshot.

    Returns ``None`` if the envelope lacks a usable ``booking_id``. The
    ``fallback_args`` (executor-resolved arguments for the call) supplies
    fields the server may not echo back — ``party_size``, ``booking_date``,
    ``slot_time``.
    """
    blob = _parse_envelope_data(envelope)

    bid: str | None = None
    res_id: str | None = None
    res_name: str | None = None
    slot_time: str | None = None
    booking_date: str | None = None
    party_size: int | None = None
    status: str | None = None
    final_amount: float | None = None
    payment_status: str | None = None
    confirmation_url: str | None = None
    cancellation_url: str | None = None
    message: str | None = None

    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        if not bid:
            cand = obj.get("booking_id") or obj.get("order_id")
            if cand is not None and str(cand).strip():
                bid = str(cand).strip()
        if not res_id:
            cand = obj.get("res_id") or obj.get("restaurant_id")
            if cand is not None and str(cand).strip():
                res_id = str(cand).strip()
        if not res_name:
            cand = obj.get("res_name") or obj.get("restaurant_name") or obj.get("name")
            if isinstance(cand, str) and cand.strip():
                res_name = cand.strip()
        if not slot_time:
            cand = obj.get("slot_time") or obj.get("time")
            if isinstance(cand, str) and cand.strip():
                slot_time = cand.strip()
        if not booking_date:
            cand = obj.get("booking_date") or obj.get("date")
            if isinstance(cand, str) and cand.strip():
                booking_date = cand.strip()
        if party_size is None:
            cand = obj.get("party_size") or obj.get("guests")
            if isinstance(cand, (int, float)):
                party_size = int(cand)
        if not status:
            cand = obj.get("booking_status") or obj.get("status")
            if isinstance(cand, str) and cand.strip():
                status = cand.strip()
        if final_amount is None:
            cand = obj.get("final_amount") or obj.get("amount") or obj.get("total")
            if isinstance(cand, (int, float)):
                final_amount = float(cand)
        if not payment_status:
            cand = obj.get("payment_status")
            if isinstance(cand, str) and cand.strip():
                payment_status = cand.strip()
        if not confirmation_url:
            cand = obj.get("confirmation_url") or obj.get("booking_url") or obj.get("web_url")
            if isinstance(cand, str) and cand.strip():
                confirmation_url = cand.strip()
        if not cancellation_url:
            cand = obj.get("cancellation_url") or obj.get("cancel_url")
            if isinstance(cand, str) and cand.strip():
                cancellation_url = cand.strip()
        if not message:
            cand = obj.get("message")
            if isinstance(cand, str) and cand.strip():
                message = cand.strip()

    if not bid:
        return None

    if fallback_args:
        if not slot_time:
            cand = fallback_args.get("slot_time")
            if isinstance(cand, str) and cand.strip():
                slot_time = cand.strip()
        if not booking_date:
            cand = fallback_args.get("booking_date")
            if isinstance(cand, str) and cand.strip():
                booking_date = cand.strip()
        if party_size is None:
            cand = fallback_args.get("party_size")
            if isinstance(cand, (int, float)):
                party_size = int(cand)
        if not res_id:
            cand = fallback_args.get("res_id")
            if cand is not None and str(cand).strip():
                res_id = str(cand).strip()

    snap: dict[str, Any] = {"booking_id": bid}
    if res_id:
        snap["res_id"] = res_id
    if res_name:
        snap["restaurant_name"] = res_name
    if booking_date:
        snap["booking_date"] = booking_date
    if slot_time:
        snap["slot_time"] = slot_time
    if party_size is not None:
        snap["party_size"] = party_size
    if status:
        snap["status"] = status
    if final_amount is not None:
        snap["final_amount"] = final_amount
    if payment_status:
        snap["payment_status"] = payment_status
    if confirmation_url:
        snap["confirmation_url"] = confirmation_url
    if cancellation_url:
        snap["cancellation_url"] = cancellation_url
    if message:
        snap["message"] = message
    return snap


# ---------------------------------------------------------------------------
# Location seeding. The Google geocoder is best-effort and never blocks
# the tool plan — if it fails we still ship the request with whatever
# coords the user / session already provided.
# ---------------------------------------------------------------------------
def _geocode_via_google(location_text: str) -> tuple[float, float] | None:
    try:
        from companion_api.google_maps import GeoPoint, geocode_address
    except Exception:  # noqa: BLE001
        return None
    try:
        point = geocode_address(location_text)
    except Exception:  # noqa: BLE001
        logger.warning("Google Maps geocode failed for %r", location_text, exc_info=True)
        return None
    if isinstance(point, GeoPoint):
        return (point.latitude, point.longitude)
    return None


def _coords_from_user_geo(user_geo: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(user_geo, dict):
        return None
    for lat_key, lng_key in (("latitude", "longitude"), ("lat", "lng")):
        lat, lng = user_geo.get(lat_key), user_geo.get(lng_key)
        if lat is None or lng is None:
            continue
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            continue
    return None


def _resolve_coords(
    *,
    placeholder_values: dict[str, str],
    location_text: str | None,
    user_geo: dict[str, Any] | None,
) -> tuple[tuple[float, float] | None, str | None]:
    """Resolve the lat/lng to seed into ``__USER_LAT__`` / ``__USER_LNG__``.

    Precedence:
      1. **Planner-detected typed location wins** — if ``location_text`` is set
         the user explicitly named a place ("near Saket", "in Bandra"), so we
         geocode that and ignore live GPS. Without this, asking for restaurants
         in another city while at home would silently search the user's current
         neighbourhood.
      2. Live GPS (``user_geo``) — used when the user gave no location hint.
      3. Carry-over from prior turns' session_state — used when neither of the
         above is available (e.g. a follow-up turn with no fresh GPS).

    Returns ``(coords, source)`` so callers can log which path won.
    """
    text = (location_text or "").strip()
    if text:
        geocoded = _geocode_via_google(text)
        if geocoded:
            return geocoded, "geocoded_text"
        # Geocoding failed (no key, ZERO_RESULTS, network blip). Fall back
        # to GPS rather than search a stale prior coord set — at least the
        # user's actual area beats sticking with last-turn's location.

    geo = _coords_from_user_geo(user_geo)
    if geo:
        return geo, "user_geo"

    lat = placeholder_values.get("__USER_LAT__")
    lng = placeholder_values.get("__USER_LNG__")
    if lat and lng:
        try:
            return (float(lat), float(lng)), "session_carry"
        except ValueError:
            pass

    return None, None


# ---------------------------------------------------------------------------
# Placeholder substitution — same shape as Zomato's helpers but with the
# District-specific alias map. Anything still unresolved after substitution
# causes the step to be skipped (instead of sent with a literal
# ``"__USER_LAT__"`` string that would 422 on the server).
# ---------------------------------------------------------------------------
def _unresolved_district_placeholders(obj: Any, known: dict[str, str]) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, str) and obj.startswith("__") and obj.endswith("__"):
        if obj not in known:
            found.add(obj)
        return found
    if isinstance(obj, dict):
        for value in obj.values():
            found |= _unresolved_district_placeholders(value, known)
    elif isinstance(obj, list):
        for value in obj:
            found |= _unresolved_district_placeholders(value, known)
    return found


def _merge_recent(
    existing: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    *,
    key: str,
    cap: int,
) -> list[dict[str, Any]]:
    """LRU merge: most-recent at the front, dedupe by ``key``."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in new_rows + existing:
        if not isinstance(row, dict):
            continue
        ident = str(row.get(key) or "").strip()
        if not ident or ident in seen:
            continue
        seen.add(ident)
        merged.append(row)
        if len(merged) >= cap:
            break
    return merged


# ---------------------------------------------------------------------------
# Main session runner
# ---------------------------------------------------------------------------
async def _run_district_session_inner(
    tool_plan: list[dict[str, Any]],
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None = None,
    location_text: str | None = None,
    user_geo: dict[str, Any] | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_movies: list[dict[str, Any]] | None = None,
    session_theatres: list[dict[str, Any]] | None = None,
    session_events: list[dict[str, Any]] | None = None,
    session_venues: list[dict[str, Any]] | None = None,
    session_artists: list[dict[str, Any]] | None = None,
    session_reservation: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:
        raise RuntimeError(
            "Python MCP SDK is not installed. Use Python 3.10+ and install requirements.txt."
        ) from e

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_location_text": location_text,
        "executor": "district.mcp_executor",
    }

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError("mcp-remote is not installed — run npm install at the repo root.")

    # Cold-container guard: hydrate /tmp/.mcp-auth from Secrets Manager when
    # the current-version tokens file isn't already present locally.
    ensure_mcp_tokens_on_disk(repo_root, mcp_url=mcp_url, user_id=user_id)

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    # ---- Seed placeholders from the carried-forward session ----
    placeholder_values: dict[str, str] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k.startswith("__") and k.endswith("__"):
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()

    # Seed session-index lists (LRU; executor merges on top after each call).
    recent_restaurants = list(session_restaurants or [])
    recent_movies = list(session_movies or [])
    recent_theatres = list(session_theatres or [])
    recent_events = list(session_events or [])
    recent_venues = list(session_venues or [])
    recent_artists = list(session_artists or [])
    reservation_snapshot: dict[str, Any] | None = (
        dict(session_reservation) if isinstance(session_reservation, dict) else None
    )

    # ---- Location resolution -------------------------------------------
    # Same auto-seed role as Zomato's __ADDRESS_ID__: the user shouldn't
    # have to think about coords any more than they think about address
    # IDs. The planner just emits the placeholder; we substitute the
    # current value the same way.
    coords, coords_source = _resolve_coords(
        placeholder_values=placeholder_values,
        location_text=location_text,
        user_geo=user_geo,
    )
    if coords:
        placeholder_values["__USER_LAT__"] = str(coords[0])
        placeholder_values["__USER_LNG__"] = str(coords[1])
        diagnostics["resolved_location"] = {
            "latitude": coords[0],
            "longitude": coords[1],
            "source": coords_source,  # "geocoded_text" | "user_geo" | "session_carry"
        }
    elif location_text:
        diagnostics["location_resolution"] = "failed"
        diagnostics["location_hint"] = (
            "District needs a specific locality (e.g. Koramangala, Bandra). "
            "Pass geo coordinates or a neighborhood name."
        )

    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())

    # ---- Execute the plan ----------------------------------------------
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS)
            step_index = 0

            for step in tool_plan:
                tool = str(step.get("tool") or "").strip()
                if not tool:
                    continue
                raw_args = dict(step.get("arguments") or {})

                # Clear any placeholder this tool is about to *issue* so the
                # next learn-from-payload walk re-learns the fresh value
                # instead of sticking with a stale prior-turn id.
                for ph in _DISTRICT_TOOL_ISSUES_PLACEHOLDER.get(tool, ()):
                    placeholder_values.pop(ph, None)

                # Substitute placeholders, then coerce lat/lng strings →
                # floats so the MCP server doesn't reject the request.
                exec_args = substitute_placeholders(copy.deepcopy(raw_args), placeholder_values)
                exec_args = _coerce_latlng_floats(
                    exec_args if isinstance(exec_args, dict) else {}
                )

                unresolved = _unresolved_district_placeholders(exec_args, placeholder_values)
                if unresolved:
                    diagnostics.setdefault("skipped_tools", []).append(
                        {
                            "tool": tool,
                            "reason": "unresolved_placeholders",
                            "placeholders": sorted(unresolved),
                        }
                    )
                    step_index += 1
                    continue

                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=exec_args or {}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(result)
                step_results.append(
                    {
                        "tool": tool,
                        "arguments": exec_args or {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1

                if _district_is_error(envelope):
                    continue

                # Post-call harvesting: placeholders + session indexes.
                _learn_district_placeholders(placeholder_values, envelope)

                if tool == "search_dining_restaurants":
                    new_rows = _collect_restaurants(envelope)
                    if new_rows:
                        recent_restaurants = _merge_recent_restaurants(
                            recent_restaurants, new_rows, cap=_MAX_REMEMBERED
                        )
                if tool == "get_dining_restaurants_details":
                    # Detail call still surfaces enriched rows we can stash.
                    new_rows = _collect_restaurants(envelope)
                    if new_rows:
                        recent_restaurants = _merge_recent_restaurants(
                            recent_restaurants, new_rows, cap=_MAX_REMEMBERED
                        )
                if tool == "search_movies":
                    new_rows = _collect_movies(envelope)
                    if new_rows:
                        recent_movies = _merge_recent(
                            recent_movies, new_rows, key="movie_id", cap=_MAX_REMEMBERED
                        )
                if tool == "search_theatres":
                    new_rows = _collect_theatres(envelope)
                    if new_rows:
                        recent_theatres = _merge_recent(
                            recent_theatres, new_rows, key="theatre_id", cap=_MAX_REMEMBERED
                        )
                if tool == "search_events":
                    new_rows = _collect_events(envelope)
                    if new_rows:
                        recent_events = _merge_recent(
                            recent_events, new_rows, key="event_id", cap=_MAX_REMEMBERED
                        )
                if tool == "search_event_venues":
                    new_rows = _collect_venues(envelope)
                    if new_rows:
                        recent_venues = _merge_recent(
                            recent_venues, new_rows, key="venue_id", cap=_MAX_REMEMBERED
                        )
                if tool == "search_artists":
                    new_rows = _collect_artists(envelope)
                    if new_rows:
                        recent_artists = _merge_recent(
                            recent_artists, new_rows, key="artist_id", cap=_MAX_REMEMBERED
                        )
                if tool in {"create_table_reservation_booking", "get_booking_details"}:
                    snap = _extract_reservation_snapshot(
                        envelope,
                        fallback_args=exec_args if isinstance(exec_args, dict) else None,
                    )
                    if snap is not None:
                        reservation_snapshot = snap
                        diagnostics["fresh_reservation_captured"] = True

    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    diagnostics["session_placeholders"] = {
        k: v for k, v in placeholder_values.items() if k in _DISTRICT_SESSION_PERSISTENT
    }
    if recent_restaurants:
        diagnostics["session_restaurants"] = list(recent_restaurants)
    if recent_movies:
        diagnostics["session_movies"] = list(recent_movies)
    if recent_theatres:
        diagnostics["session_theatres"] = list(recent_theatres)
    if recent_events:
        diagnostics["session_events"] = list(recent_events)
    if recent_venues:
        diagnostics["session_venues"] = list(recent_venues)
    if recent_artists:
        diagnostics["session_artists"] = list(recent_artists)
    if reservation_snapshot:
        diagnostics["session_reservation"] = reservation_snapshot
    return step_results, diagnostics


def run_district_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    location_text: str | None = None,
    user_geo: dict[str, Any] | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_restaurants: list[dict[str, Any]] | None = None,
    session_movies: list[dict[str, Any]] | None = None,
    session_theatres: list[dict[str, Any]] | None = None,
    session_events: list[dict[str, Any]] | None = None,
    session_venues: list[dict[str, Any]] | None = None,
    session_artists: list[dict[str, Any]] | None = None,
    session_reservation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("DISTRICT_MCP_URL") or _DEFAULT_DISTRICT_MCP_URL).strip()
    node = node_binary or os.getenv("NODE_BINARY", "node")

    cleaned: list[dict[str, Any]] = []
    for s in tool_plan:
        if isinstance(s, dict):
            tn = str(s.get("tool") or "").strip()
            if tn:
                cleaned.append({"tool": tn, "arguments": dict(s.get("arguments") or {})})

    try:
        steps, diagnostics = asyncio.run(
            _run_district_session_inner(
                cleaned,
                root,
                url,
                node,
                user_id,
                location_text=location_text,
                user_geo=user_geo,
                session_placeholders=session_placeholders,
                session_restaurants=session_restaurants,
                session_movies=session_movies,
                session_theatres=session_theatres,
                session_events=session_events,
                session_venues=session_venues,
                session_artists=session_artists,
                session_reservation=session_reservation,
            )
        )
        skipped = bool(diagnostics.get("skipped_tools"))
        out: dict[str, Any] = {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
        # `needs_location` propagation — same UX hook the Maps agent uses.
        # When every skipped step bailed on an unresolved __USER_LAT__ /
        # __USER_LNG__, the frontend should auto-prompt for GPS.
        no_coords = "resolved_location" not in diagnostics
        skipped_for_location = bool(diagnostics.get("skipped_tools")) and all(
            "__USER_LAT__" in (entry or {}).get("placeholders", [])
            or "__USER_LNG__" in (entry or {}).get("placeholders", [])
            for entry in diagnostics.get("skipped_tools") or []
        )
        if no_coords and skipped_for_location:
            out["error_code"] = "needs_location"
        return out
    except BaseException as e:
        logger.exception("District MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e, url),
            "steps": [],
            "diagnostics": {},
        }


def list_district_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    from app.services.zomato.mcp_executor import list_mcp_tools_sync

    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("DISTRICT_MCP_URL") or _DEFAULT_DISTRICT_MCP_URL).strip()
    return list_mcp_tools_sync(
        repo_root=root,
        mcp_url=url,
        node_binary=node_binary,
        user_id=user_id,
    )
