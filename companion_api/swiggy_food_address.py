"""Per-user Swiggy Food default delivery address — the cart flow's source of truth.

Mirror of ``instamart_address.py``. Primary store is DynamoDB
(ProfileCacheRepository, ``swiggy_food_default_address`` attribute on the user's
profile item); for local dev — where the DynamoDB table isn't provisioned — we
transparently fall back to a JSON file under ``.swiggy_food_prefs/`` so the
onboarding flow is fully testable locally and identical in prod.

A tiny in-process TTL cache fronts reads so the executor can pin the address on
every turn without a round-trip; saves invalidate the cache immediately.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FIELD = "swiggy_food_default_address"
_TTL_SECONDS = 60
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _clean(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    aid = str(record.get("address_id") or "").strip()
    if not aid:
        return None
    return {
        "address_id": aid,
        "tag": (str(record.get("tag")).strip() or None) if record.get("tag") else None,
        "line": (str(record.get("line")).strip() or None) if record.get("line") else None,
        "saved_at": record.get("saved_at"),
    }


def _local_path(user_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", user_id)[:80] or "user"
    base = Path(__file__).resolve().parents[1] / ".swiggy_food_prefs"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe}.json"


def _read_local(user_id: str) -> dict[str, Any] | None:
    try:
        return _clean(json.loads(_local_path(user_id).read_text(encoding="utf-8")))
    except Exception:
        return None


def _write_local(user_id: str, record: dict[str, Any]) -> None:
    _local_path(user_id).write_text(json.dumps(record), encoding="utf-8")


def get_default_swiggy_food_address(user_id: str) -> dict[str, Any] | None:
    """The user's saved default Swiggy Food address (cached), or None."""
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


def save_default_swiggy_food_address(
    user_id: str, *, address_id: str, tag: str | None = None, line: str | None = None
) -> dict[str, Any]:
    """Persist the chosen default address (DynamoDB, local-file fallback) and
    invalidate the cache so it takes effect on the next turn."""
    record = {
        "address_id": address_id.strip(),
        "tag": (tag or "").strip() or None,
        "line": (line or "").strip() or None,
        "saved_at": int(time.time()),
    }
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
