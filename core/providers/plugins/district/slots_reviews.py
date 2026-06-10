"""District slot + critic-review card builders.

`get_table_reservation_slots` and `get_movie_critique_reviews` returned data
the user never saw (no card, truncated narrator blob). Defensive alias walks —
District envelopes vary and sometimes arrive as JSON strings (`envelope_data`).
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.plugins.district.discovery import envelope_data

_MAX_SLOTS = 24
_MAX_REVIEWS = 8

_SLOT_TIME_KEYS = ("slot_time", "time", "slot", "start_time", "display_time")
_REVIEW_CRITIC_KEYS = ("critic_name", "critic", "author", "reviewer")
_REVIEW_SOURCE_KEYS = ("publication", "source", "publisher")
_REVIEW_RATING_KEYS = ("rating", "score", "stars")
_REVIEW_TEXT_KEYS = ("review", "snippet", "text", "quote", "description", "headline")


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def slot_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized reservation slots: `{time, label?, available?}` rows."""
    data = envelope_data(envelope)
    if data is None or envelope.get("is_error"):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for obj in _walk(data):
        if not isinstance(obj, dict) or len(out) >= _MAX_SLOTS:
            continue
        time = _first(obj, _SLOT_TIME_KEYS)
        if time in (None, "") or str(time) in seen:
            continue
        seen.add(str(time))
        entry: dict[str, Any] = {"time": str(time)}
        if obj.get("label") or obj.get("name"):
            entry["label"] = str(obj.get("label") or obj.get("name"))
        if obj.get("available") is not None or obj.get("is_available") is not None:
            entry["available"] = bool(
                obj.get("available") if obj.get("available") is not None else obj["is_available"]
            )
        out.append(entry)
    return out


def review_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized critic reviews: `{critic?, publication?, rating?, snippet?}` rows."""
    data = envelope_data(envelope)
    if data is None or envelope.get("is_error"):
        return []
    out: list[dict[str, Any]] = []
    for obj in _walk(data):
        if not isinstance(obj, dict) or len(out) >= _MAX_REVIEWS:
            continue
        text = _first(obj, _REVIEW_TEXT_KEYS)
        rating = _first(obj, _REVIEW_RATING_KEYS)
        if text is None and rating is None:
            continue
        critic = _first(obj, _REVIEW_CRITIC_KEYS)
        source = _first(obj, _REVIEW_SOURCE_KEYS)
        if text is None and critic is None and source is None:
            continue
        entry: dict[str, Any] = {}
        if critic:
            entry["critic"] = str(critic)
        if source:
            entry["publication"] = str(source)
        if rating not in (None, ""):
            entry["rating"] = str(rating)
        if text:
            entry["snippet"] = str(text)[:240]
        if entry:
            out.append(entry)
    return out


def build_slot_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool != "get_table_reservation_slots" or not tr.ok:
            continue
        slots = slot_entries(tr.data if isinstance(tr.data, dict) else {})
        if not slots:
            continue
        args = tr.arguments or {}
        payload: dict[str, Any] = {
            "kind": "slot_cards",
            "provider": "district",
            "source": f"district.{tr.tool}",
            "slots": slots,
        }
        for field, key in (
            ("res_name", "res_name"),
            ("booking_date", "booking_date"),
            ("party_size", "party_size"),
        ):
            if args.get(key) not in (None, ""):
                payload[field] = args[key]
        out.append(Attachment(**payload))
    return out


def build_review_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool != "get_movie_critique_reviews" or not tr.ok:
            continue
        reviews = review_entries(tr.data if isinstance(tr.data, dict) else {})
        if reviews:
            out.append(
                Attachment(
                    kind="review_cards",
                    provider="district",
                    source=f"district.{tr.tool}",
                    reviews=reviews,
                )
            )
    return out
