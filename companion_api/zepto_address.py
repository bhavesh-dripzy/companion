"""Per-user Zepto default delivery address — the cart flow's source of truth.

Mirrors ``instamart_address.py``. Zepto's ``select_saved_address`` binds BOTH
the store/serviceability AND the order's delivery doorstep from a single saved
address id — so (unlike the old GPS-point model) Zepto's per-user source of
truth is a saved-address id (+ a friendly tag/line for display), captured once
during onboarding. This unifies Zepto with Instamart's address model.

Primary store is DynamoDB (ProfileCacheRepository, ``zepto_default_address``
attribute). For local dev — where the table isn't provisioned — we fall back to
a JSON file under ``.zepto_prefs/``. A tiny in-process TTL cache fronts reads so
the executor can pin the address on every turn without a round-trip.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FIELD = "zepto_default_address"
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
    base = Path(__file__).resolve().parents[1] / ".zepto_prefs"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe}.json"


def _read_local(user_id: str) -> dict[str, Any] | None:
    try:
        return _clean(json.loads(_local_path(user_id).read_text(encoding="utf-8")))
    except Exception:
        return None


def _write_local(user_id: str, record: dict[str, Any]) -> None:
    _local_path(user_id).write_text(json.dumps(record), encoding="utf-8")


def get_default_zepto_address(user_id: str) -> dict[str, Any] | None:
    """The user's saved default Zepto delivery address (cached), or None."""
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


def save_default_zepto_address(
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


def clear_default_zepto_address(user_id: str) -> None:
    """Drop the saved default address (e.g. it was deleted/renamed on the Zepto app,
    so it no longer appears in the live saved-address list). Clears DynamoDB + the
    local fallback and invalidates the cache so the next turn re-resolves from the
    live list / GPS instead of pinning a dead id."""
    if not user_id:
        return
    try:
        from companion_api.profile_cache import ProfileCacheRepository

        ProfileCacheRepository().update_fields(user_id, {_FIELD: None})
    except Exception:
        logger.warning("DynamoDB clear unavailable; clearing local fallback", exc_info=True)
    try:
        _local_path(user_id).unlink(missing_ok=True)
    except Exception:
        pass
    _cache[user_id] = (time.time() + _TTL_SECONDS, None)


def invalidate(user_id: str) -> None:
    _cache.pop(user_id, None)
