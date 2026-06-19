"""Per-user District default location — the discovery flow's source of truth.

District search tools (movies / events / dining / theatres) take a numeric
``{latitude, longitude}`` location; District has no saved-address concept the way
Swiggy Instamart does. So District's per-user source of truth is a
``{latitude, longitude}`` point (captured once during onboarding), with an
optional human label. This mirrors ``zepto_location`` in spirit.

Primary store is DynamoDB (ProfileCacheRepository, ``district_default_location``
attribute on the user's profile item). For local dev — where the DynamoDB table
isn't provisioned — we transparently fall back to a JSON file under
``.district_prefs/``, so the onboarding flow is fully testable locally and
identical in prod. A tiny in-process TTL cache fronts reads so the executor can
pin the location on every turn without a round-trip; saves invalidate it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FIELD = "district_default_location"
_TTL_SECONDS = 60
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _coord(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Reject obviously bogus coordinates (0,0 / out of range).
    if f != f or abs(f) < 1e-6 or abs(f) > 180:
        return None
    return round(f, 6)


def _clean(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    lat = _coord(record.get("latitude"))
    lng = _coord(record.get("longitude"))
    if lat is None or lng is None:
        return None
    return {
        "latitude": lat,
        "longitude": lng,
        "label": (str(record.get("label")).strip() or None) if record.get("label") else None,
        "saved_at": record.get("saved_at"),
    }


def _local_path(user_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", user_id)[:80] or "user"
    base = Path(__file__).resolve().parents[1] / ".district_prefs"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe}.json"


def _read_local(user_id: str) -> dict[str, Any] | None:
    try:
        return _clean(json.loads(_local_path(user_id).read_text(encoding="utf-8")))
    except Exception:
        return None


def _write_local(user_id: str, record: dict[str, Any]) -> None:
    _local_path(user_id).write_text(json.dumps(record), encoding="utf-8")


def get_default_district_location(user_id: str) -> dict[str, Any] | None:
    """The user's saved default District location (cached), or None."""
    if not user_id:
        return None
    hit = _cache.get(user_id)
    if hit and hit[0] > time.time():
        return hit[1]
    record: dict[str, Any] | None = None
    try:
        from companion_api.profile_cache import ProfileCacheRepository

        profile = ProfileCacheRepository().get(user_id) or {}
        record = _clean(profile.get(_FIELD))
    except Exception:
        logger.debug("DynamoDB profile read unavailable; using local fallback", exc_info=True)
    if record is None:
        record = _read_local(user_id)
    _cache[user_id] = (time.time() + _TTL_SECONDS, record)
    return record


def save_default_district_location(
    user_id: str,
    *,
    latitude: float,
    longitude: float,
    label: str | None = None,
) -> dict[str, Any]:
    """Persist the chosen default location (DynamoDB, local-file fallback) and
    invalidate the cache so it takes effect on the next turn."""
    record = _clean(
        {
            "latitude": latitude,
            "longitude": longitude,
            "label": label,
            "saved_at": int(time.time()),
        }
    )
    if record is None:
        raise ValueError("latitude/longitude are invalid")
    wrote = False
    try:
        from companion_api.profile_cache import ProfileCacheRepository

        ProfileCacheRepository().update_fields(user_id, {_FIELD: record})
        wrote = True
    except Exception:
        logger.warning("DynamoDB save unavailable; writing local fallback", exc_info=True)
    if not wrote:
        _write_local(user_id, record)
    _cache[user_id] = (time.time() + _TTL_SECONDS, record)
    return record


def invalidate(user_id: str) -> None:
    _cache.pop(user_id, None)
