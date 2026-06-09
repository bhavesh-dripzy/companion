"""District discovery-card builders: dining, movie, theatre, show.

Clean port of `agents.pipeline._build_district_{dining,movie,theatre,show}_card`,
adapted to operate on `ToolResult`s. Validated against real District envelopes
(search_dining_restaurants → restaurants[]; search_movies → movies[]).
"""

from __future__ import annotations

import json
from typing import Any


def envelope_data(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """District sometimes returns `data` as a JSON string; normalize to a dict."""
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return data if isinstance(data, dict) else None


def dining_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    rid = item.get("res_id") or item.get("restaurant_id") or item.get("id")
    name = item.get("res_name") or item.get("name") or item.get("restaurant_name")
    if rid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"res_id": str(rid).strip(), "name": name.strip()}
    rating = item.get("rating") or item.get("avg_rating")
    if isinstance(rating, int | float) and rating > 0:
        out["rating"] = round(float(rating), 1)
    cuisines = item.get("cuisine") or item.get("cuisines")
    if isinstance(cuisines, list):
        clean = [c.strip() for c in cuisines if isinstance(c, str) and c.strip()]
        if clean:
            out["cuisines"] = clean[:4]
    elif isinstance(cuisines, str) and cuisines.strip():
        out["cuisines"] = [cuisines.strip()]
    locality = item.get("locality") or item.get("area") or item.get("sub_zone")
    if isinstance(locality, str) and locality.strip():
        out["locality"] = locality.strip()
    price = item.get("price_for_two") or item.get("cost_for_two")
    if isinstance(price, int | float | str) and str(price).strip():
        out["price_for_two"] = price
    image = item.get("image_url") or item.get("photo_url")
    if isinstance(image, str) and image.strip():
        out["image_url"] = image.strip()
    bookable = item.get("bookable_via_agent")
    if isinstance(bookable, bool):
        out["bookable_via_agent"] = bookable
    res_avail = item.get("is_table_reservation_available")
    if isinstance(res_avail, bool):
        out["is_table_reservation_available"] = res_avail
    distance = item.get("distance")
    if isinstance(distance, int | float) and distance >= 0:
        out["distance"] = round(float(distance), 2)
    elif isinstance(distance, str) and distance.strip():
        out["distance"] = distance.strip()
    for src in ("web_url", "res_page_url", "booking_url", "best_offer"):
        val = item.get(src)
        if isinstance(val, str) and val.strip():
            out[src if src != "res_page_url" else "web_url"] = val.strip()
            if src == "web_url":
                break
    return out


def movie_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    mid = item.get("movie_id") or item.get("id") or item.get("content_id")
    name = item.get("movie_name") or item.get("name") or item.get("title")
    if mid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"movie_id": str(mid).strip(), "name": name.strip()}
    for src, dst in (
        ("language", "languages"),
        ("languages", "languages"),
        ("genre", "genres"),
        ("genres", "genres"),
    ):
        val = item.get(src)
        if dst in out:
            continue
        if isinstance(val, list):
            clean = [v.strip() for v in val if isinstance(v, str) and v.strip()]
            if clean:
                out[dst] = clean[:4]
        elif isinstance(val, str) and val.strip():
            out[dst] = [val.strip()]
    duration = item.get("duration_minutes") or item.get("duration")
    if isinstance(duration, int | float) and duration > 0:
        out["duration_minutes"] = int(duration)
    rating = item.get("rating") or item.get("imdb_rating")
    if isinstance(rating, int | float) and rating > 0:
        out["rating"] = round(float(rating), 1)
    for src, dst in (
        ("cbfc_certification", "cbfc_certification"),
        ("release_date", "release_date"),
    ):
        val = item.get(src)
        if isinstance(val, str) and val.strip():
            out[dst] = val.strip()
    poster = item.get("poster_url") or item.get("image_url") or item.get("spotlight_thumbnail_url")
    if isinstance(poster, str) and poster.strip():
        out["poster_url"] = poster.strip()
    for src in ("spotlight_video_url", "web_url"):
        val = item.get(src)
        if isinstance(val, str) and val.strip():
            out[src] = val.strip()
    return out


def theatre_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    tid = (
        item.get("theatre_id") or item.get("theater_id") or item.get("id") or item.get("cinema_id")
    )
    name = item.get("theatre_name") or item.get("name") or item.get("theater_name")
    if tid is None or not isinstance(name, str) or not name.strip():
        return None
    out: dict[str, Any] = {"theatre_id": str(tid).strip(), "name": name.strip()}
    locality = item.get("locality") or item.get("area")
    if isinstance(locality, str) and locality.strip():
        out["locality"] = locality.strip()
    distance = item.get("distance")
    if isinstance(distance, int | float) and distance >= 0:
        out["distance"] = round(float(distance), 2)
    elif isinstance(distance, str) and distance.strip():
        out["distance"] = distance.strip()
    for src in ("image_url", "photo_url", "web_url"):
        val = item.get(src)
        if isinstance(val, str) and val.strip():
            out["image_url" if src == "photo_url" else src] = val.strip()
    return out


_SHOW_FIELDS = (
    "movie_id",
    "movie_name",
    "theatre_id",
    "theatre_name",
    "show_date",
    "show_start_time",
    "screen_type",
    "language",
    "min_price",
    "max_price",
    "seat_availability",
    "booking_url",
    "web_url",
)


def show_card(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    sid = item.get("show_id") or item.get("id")
    if sid is None:
        return None
    out: dict[str, Any] = {"show_id": str(sid).strip()}
    for key in _SHOW_FIELDS:
        val = item.get(key)
        if val in (None, ""):
            continue
        if isinstance(val, str):
            if val.strip():
                out[key] = val.strip()
        else:
            out[key] = val
    return out
