"""
District MCP tool execution via mcp-remote + stdio.

Handles dining discovery/table reservations and movie/theatre/show discovery.
Persists cross-turn state via ``session_placeholders``, ``session_restaurants``,
``session_movies``, and ``session_theatres`` (same echo contract as Zomato/Swiggy).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("DISTRICT_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("DISTRICT_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30")
)
_DEFAULT_DISTRICT_MCP_URL = "https://mcp-server.district.in/mcp"
_MAX_REMEMBERED = max(1, int(os.getenv("DISTRICT_REMEMBERED_CAP", "8")))

from app.services.zomato.mcp_executor import (  # noqa: E402
    _humanize_mcp_failure,
    _merge_recent_restaurants,
    _stdio_env,
    _tool_result_payload,
    _walk_json,
    repo_root_from_settings,
    substitute_placeholders,
)

_DISTRICT_PLACEHOLDER_ALIASES: dict[str, tuple[str, ...]] = {
    "__RES_ID__": ("res_id", "restaurant_id", "restaurantId"),
    "__BOOKING_ID__": ("booking_id", "order_id", "orderId"),
    "__MOVIE_ID__": ("movie_id", "movieId", "content_id", "contentId"),
    "__THEATRE_ID__": ("theatre_id", "theater_id", "theatreId", "cinema_id"),
    "__SHOW_ID__": ("show_id", "showId"),
}

_DISTRICT_SESSION_PERSISTENT = frozenset(
    {
        "__USER_LAT__",
        "__USER_LNG__",
        "__RES_ID__",
        "__BOOKING_ID__",
        "__MOVIE_ID__",
        "__THEATRE_ID__",
        "__SHOW_ID__",
    }
)

_DISTRICT_TOOL_ISSUES_PLACEHOLDER: dict[str, tuple[str, ...]] = {
    "search_dining_restaurants": ("__RES_ID__",),
    "search_movies": ("__MOVIE_ID__",),
    "search_theatres": ("__THEATRE_ID__",),
    "create_table_reservation_booking": ("__BOOKING_ID__",),
}

_DINING_LOCATION_TOOLS = frozenset(
    {
        "get_dynamic_filters",
        "search_dining_restaurants",
        "get_dining_restaurants_details",
        "get_dynamic_filters_movie",
    }
)
_MOVIE_LOCATION_TOOLS = frozenset(
    {
        "search_movies",
        "search_theatres",
        "search_shows",
        "get_dynamic_filters_movie",
    }
)

# Fallback centroids for common localities — used only when the Google Maps
# Geocoding API is unavailable (no key, offline test runs). The live path
# prefers Google's Geocoding API so any text the user types ("near Galleria
# market", "Saket select citywalk", etc.) gets resolved with proper precision.
_LOCALITY_COORDS: dict[str, tuple[float, float]] = {
    "koramangala": (12.9352, 77.6245),
    "indiranagar": (12.9784, 77.6408),
    "whitefield": (12.9698, 77.7499),
    "mg road": (12.9750, 77.6063),
    "bandra": (19.0544, 72.8406),
    "andheri": (19.1136, 72.8697),
    "connaught place": (28.6315, 77.2167),
    "cyber hub": (28.4945, 77.0895),
    "sector 38": (28.4455, 77.0750),
    "gurugram": (28.4595, 77.0266),
    "gurgaon": (28.4595, 77.0266),
    "hauz khas": (28.5494, 77.2001),
    "jayanagar": (12.9250, 77.5937),
}


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
        # Booking nested under result
        result = obj.get("result")
        if isinstance(result, dict):
            bid = result.get("booking_id") or result.get("order_id")
            if bid and "__BOOKING_ID__" not in values:
                values["__BOOKING_ID__"] = str(bid).strip()


def _collect_restaurants(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    blob = _parse_envelope_data(envelope)
    rows: list[dict[str, Any]] = []
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        raw = obj.get("restaurants")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            rid = item.get("res_id") or item.get("id")
            name = item.get("res_name") or item.get("name") or item.get("restaurant_name")
            if rid is None or not name:
                continue
            rows.append(
                {
                    "res_id": str(rid).strip(),
                    "name": str(name).strip(),
                    "rating": item.get("rating"),
                    "cuisine": item.get("cuisine") or item.get("cuisines"),
                }
            )
        if rows:
            break
    return rows


def _collect_movies(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    blob = _parse_envelope_data(envelope)
    rows: list[dict[str, Any]] = []
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        raw = obj.get("movies")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            mid = item.get("movie_id") or item.get("id")
            name = item.get("movie_name") or item.get("name")
            if mid is None or not name:
                continue
            rows.append(
                {
                    "movie_id": str(mid).strip(),
                    "name": str(name).strip(),
                    "language": item.get("language"),
                }
            )
        if rows:
            break
    return rows


def _collect_theatres(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    blob = _parse_envelope_data(envelope)
    rows: list[dict[str, Any]] = []
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        raw = obj.get("theatres")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            tid = item.get("theatre_id") or item.get("id")
            name = item.get("theatre_name") or item.get("name")
            if tid is None or not name:
                continue
            rows.append(
                {
                    "theatre_id": str(tid).strip(),
                    "name": str(name).strip(),
                    "distance": item.get("distance"),
                }
            )
        if rows:
            break
    return rows


def _geocode_via_google(location_text: str) -> tuple[float, float] | None:
    """Try Google Maps Geocoding. Returns None if the API isn't configured
    or fails — callers should fall through to the local lookup table.

    Imported lazily so a missing/misbehaving ``google_maps`` module never
    breaks the District executor (which has its own offline-friendly path).
    """
    try:
        from companion_api.google_maps import GeoPoint, geocode_address
    except Exception:  # noqa: BLE001 — defensive: never let import break execution
        return None
    try:
        point = geocode_address(location_text)
    except Exception:  # noqa: BLE001 — geocoder is best-effort
        logger.warning("Google Maps geocode failed for %r", location_text, exc_info=True)
        return None
    if isinstance(point, GeoPoint):
        return (point.latitude, point.longitude)
    return None


def _geocode_from_locality_table(location_text: str) -> tuple[float, float] | None:
    text = location_text.strip().lower()
    if not text:
        return None
    for key, coords in sorted(_LOCALITY_COORDS.items(), key=lambda kv: -len(kv[0])):
        if key in text:
            return coords
    return None


def _geocode_location_text(location_text: str | None) -> tuple[float, float] | None:
    text = (location_text or "").strip()
    if not text:
        return None
    via_google = _geocode_via_google(text)
    if via_google is not None:
        return via_google
    return _geocode_from_locality_table(text)


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
) -> tuple[float, float] | None:
    geo = _coords_from_user_geo(user_geo)
    if geo:
        return geo
    lat = placeholder_values.get("__USER_LAT__")
    lng = placeholder_values.get("__USER_LNG__")
    if lat and lng:
        try:
            return float(lat), float(lng)
        except ValueError:
            pass
    return _geocode_location_text(location_text)


def _location_dict(lat: float, lng: float) -> dict[str, float]:
    return {"latitude": lat, "longitude": lng}


def _normalize_dining_filters(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("filter_name")
        values = item.get("values") or item.get("filter_values")
        if not name or not isinstance(values, list):
            continue
        clean_vals = [str(v).strip() for v in values if str(v).strip()]
        if clean_vals:
            out.append({"name": str(name).strip(), "values": clean_vals})
    return out


def _normalize_movie_filters(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("filter_name") or item.get("name")
        values = item.get("filter_values") or item.get("values")
        if not name or not isinstance(values, list):
            continue
        clean_vals = [str(v).strip() for v in values if str(v).strip()]
        if clean_vals:
            out.append({"filter_name": str(name).strip(), "filter_values": clean_vals})
    return out


def _inject_movie_ids_from_placeholders(
    filters: list[dict[str, Any]],
    placeholder_values: dict[str, str],
) -> list[dict[str, Any]]:
    out = list(filters)
    mid = placeholder_values.get("__MOVIE_ID__")
    tid = placeholder_values.get("__THEATRE_ID__")
    if mid and not any(f.get("filter_name") == "movie_id" for f in out):
        out.append({"filter_name": "movie_id", "filter_values": [mid]})
    if tid and not any(f.get("filter_name") == "theatre_id" for f in out):
        out.append({"filter_name": "theatre_id", "filter_values": [tid]})
    return out


def _prepare_tool_args(
    tool: str,
    args: dict[str, Any],
    *,
    coords: tuple[float, float] | None,
    placeholder_values: dict[str, str],
) -> dict[str, Any] | None:
    out = copy.deepcopy(args or {})
    lat, lng = coords if coords else (None, None)

    if tool in _DINING_LOCATION_TOOLS:
        if lat is None or lng is None:
            return None
        loc = _location_dict(lat, lng)
        out["user_location"] = out.get("user_location") or loc
        if tool == "get_dining_restaurants_details":
            res_ids = out.get("res_ids")
            if isinstance(res_ids, list):
                out["res_ids"] = [str(x).strip() for x in res_ids if str(x).strip()]
            elif placeholder_values.get("__RES_ID__"):
                out["res_ids"] = [placeholder_values["__RES_ID__"]]
        if tool == "search_dining_restaurants":
            out["filters"] = _normalize_dining_filters(out.get("filters"))

    if tool in _MOVIE_LOCATION_TOOLS:
        if lat is None or lng is None:
            return None
        loc = _location_dict(lat, lng)
        if tool == "get_dynamic_filters_movie":
            out["user_location"] = out.get("user_location") or loc
        else:
            out["location"] = out.get("location") or loc
        if tool in {"search_movies", "search_theatres", "search_shows"}:
            raw_filters = out.get("filters")
            if tool == "search_shows":
                filters = _normalize_movie_filters(raw_filters if isinstance(raw_filters, list) else [])
                out["filters"] = _inject_movie_ids_from_placeholders(filters, placeholder_values)
            elif tool == "search_movies":
                out["filters"] = _normalize_movie_filters(raw_filters if isinstance(raw_filters, list) else [])
            elif tool == "search_theatres":
                out["filters"] = _normalize_movie_filters(raw_filters if isinstance(raw_filters, list) else [])

    if tool == "get_table_reservation_slots":
        if not out.get("booking_date"):
            out["booking_date"] = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        if not out.get("res_id") and placeholder_values.get("__RES_ID__"):
            out["res_id"] = placeholder_values["__RES_ID__"]

    if tool == "create_table_reservation_booking":
        if out.get("res_id") is not None:
            try:
                out["res_id"] = int(out["res_id"])
            except (TypeError, ValueError):
                pass
        if not out.get("slot_id") and not out.get("slot_time"):
            return None

    if tool == "get_booking_details":
        if not out.get("booking_id") and placeholder_values.get("__BOOKING_ID__"):
            out["booking_id"] = placeholder_values["__BOOKING_ID__"]

    if tool == "get_movie_critique_reviews":
        if out.get("movie_id") is not None:
            try:
                out["movie_id"] = int(out["movie_id"])
            except (TypeError, ValueError):
                pass
        elif placeholder_values.get("__MOVIE_ID__"):
            try:
                out["movie_id"] = int(placeholder_values["__MOVIE_ID__"])
            except ValueError:
                return None

    return out


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

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    placeholder_values: dict[str, str] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k.startswith("__") and k.endswith("__"):
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()

    recent_restaurants: list[dict[str, Any]] = []
    if isinstance(session_restaurants, list):
        for entry in session_restaurants:
            if isinstance(entry, dict) and entry.get("res_id") and entry.get("name"):
                recent_restaurants.append(
                    {
                        "res_id": str(entry["res_id"]).strip(),
                        "name": str(entry["name"]).strip(),
                        **{k: v for k, v in entry.items() if k not in {"res_id", "name"}},
                    }
                )

    recent_movies: list[dict[str, Any]] = []
    if isinstance(session_movies, list):
        for entry in session_movies:
            if isinstance(entry, dict) and entry.get("movie_id") and entry.get("name"):
                recent_movies.append(
                    {
                        "movie_id": str(entry["movie_id"]).strip(),
                        "name": str(entry["name"]).strip(),
                    }
                )

    recent_theatres: list[dict[str, Any]] = []
    if isinstance(session_theatres, list):
        for entry in session_theatres:
            if isinstance(entry, dict) and entry.get("theatre_id") and entry.get("name"):
                recent_theatres.append(
                    {
                        "theatre_id": str(entry["theatre_id"]).strip(),
                        "name": str(entry["name"]).strip(),
                    }
                )

    coords = _resolve_coords(
        placeholder_values=placeholder_values,
        location_text=location_text,
        user_geo=user_geo,
    )
    if coords:
        placeholder_values["__USER_LAT__"] = str(coords[0])
        placeholder_values["__USER_LNG__"] = str(coords[1])
        diagnostics["resolved_location"] = {"latitude": coords[0], "longitude": coords[1]}
    elif location_text:
        diagnostics["location_resolution"] = "failed"
        diagnostics["location_hint"] = (
            "District needs a specific locality (e.g. Koramangala, Bandra). "
            "Pass geo coordinates or a neighborhood name."
        )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS)
            step_index = 0

            for step in tool_plan:
                tool = str(step.get("tool") or "").strip()
                if not tool:
                    continue
                raw_args = dict(step.get("arguments") or {})
                for ph in _DISTRICT_TOOL_ISSUES_PLACEHOLDER.get(tool, ()):
                    placeholder_values.pop(ph, None)

                exec_args = substitute_placeholders(raw_args, placeholder_values)
                exec_args = _prepare_tool_args(
                    tool,
                    exec_args if isinstance(exec_args, dict) else {},
                    coords=coords,
                    placeholder_values=placeholder_values,
                )
                if exec_args is None:
                    diagnostics.setdefault("skipped_tools", []).append(
                        {"tool": tool, "reason": "missing_location_or_required_args"}
                    )
                    continue

                unresolved = _unresolved_district_placeholders(exec_args, placeholder_values)
                if unresolved:
                    diagnostics.setdefault("skipped_tools", []).append(
                        {"tool": tool, "reason": "unresolved_placeholders", "placeholders": sorted(unresolved)}
                    )
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

                if not _district_is_error(envelope):
                    _learn_district_placeholders(placeholder_values, envelope)
                    if tool == "search_dining_restaurants":
                        new_restos = _collect_restaurants(envelope)
                        if new_restos:
                            recent_restaurants = _merge_recent_restaurants(
                                recent_restaurants,
                                new_restos,
                                cap=_MAX_REMEMBERED,
                            )
                    if tool == "search_movies":
                        new_movies = _collect_movies(envelope)
                        if new_movies:
                            seen = {m["movie_id"] for m in recent_movies}
                            for m in new_movies:
                                if m["movie_id"] not in seen:
                                    recent_movies.insert(0, m)
                                    seen.add(m["movie_id"])
                            recent_movies = recent_movies[:_MAX_REMEMBERED]
                    if tool == "search_theatres":
                        new_theatres = _collect_theatres(envelope)
                        if new_theatres:
                            seen = {t["theatre_id"] for t in recent_theatres}
                            for t in new_theatres:
                                if t["theatre_id"] not in seen:
                                    recent_theatres.insert(0, t)
                                    seen.add(t["theatre_id"])
                            recent_theatres = recent_theatres[:_MAX_REMEMBERED]

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
            )
        )
        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
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
