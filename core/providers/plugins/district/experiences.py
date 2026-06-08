"""District experience-card builders: event, venue, artist.

Clean port of `agents.pipeline._build_district_{event,venue,artist}_card`,
adapted to operate on `ToolResult`s. Validated against a real search_events
envelope (events[] with venue/price/date/tags/flags).
"""

from __future__ import annotations

from typing import Any

_EVENT_STR_FIELDS = (
    "description",
    "date_string",
    "venue_name",
    "locality",
    "city",
    "price_string",
    "offer_string",
    "image_url",
    "video_url",
    "web_url",
)
_EVENT_FLAGS = ("is_dining_event", "is_activity", "is_play", "is_attraction")


def event_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    eid = item.get("event_id") or item.get("id")
    name = item.get("name") or item.get("event_name") or item.get("title")
    if eid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"event_id": str(eid).strip(), "name": name.strip()}
    for key in _EVENT_STR_FIELDS:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    min_price = item.get("min_price")
    if isinstance(min_price, int | float) and min_price >= 0:
        out["min_price"] = float(min_price)
    distance = item.get("distance")
    if isinstance(distance, int | float) and distance >= 0:
        out["distance_km"] = round(float(distance), 2)
    tags = item.get("tags")
    if isinstance(tags, list):
        clean = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        if clean:
            out["tags"] = clean[:6]
    for flag in _EVENT_FLAGS:
        if item.get(flag) is True:
            out[flag] = True
    rating = item.get("rating_info")
    if isinstance(rating, dict):
        rv = rating.get("rating")
        if isinstance(rv, int | float) and rv > 0:
            out["rating"] = round(float(rv), 1)
        rc = rating.get("review_count")
        if isinstance(rc, int | float) and rc > 0:
            out["review_count"] = int(rc)
    return out


def venue_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    vid = item.get("venue_id") or item.get("id")
    name = item.get("venue_name") or item.get("name")
    if vid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"venue_id": str(vid).strip(), "name": name.strip()}
    for key in ("city", "locality", "image_url", "web_url"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    distance = item.get("distance")
    if isinstance(distance, int | float) and distance >= 0:
        out["distance_km"] = round(float(distance), 2)
    for key in ("event_names", "categories"):
        val = item.get(key)
        if isinstance(val, list):
            clean = [v.strip() for v in val if isinstance(v, str) and v.strip()]
            if clean:
                out[key] = clean[:5]
    return out


def artist_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    aid = item.get("artist_id") or item.get("id")
    name = item.get("artist_name") or item.get("name")
    if aid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"artist_id": str(aid).strip(), "name": name.strip()}
    for key in ("artist_type", "description", "image_url"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    upcoming = item.get("upcoming_event_count")
    if isinstance(upcoming, int | float) and upcoming >= 0:
        out["upcoming_event_count"] = int(upcoming)
    genres = item.get("genres")
    if isinstance(genres, list):
        clean = [g.strip() for g in genres if isinstance(g, str) and g.strip()]
        if clean:
            out["genres"] = clean[:5]
    return out
