"""Global, scalable connection-status cache via a DynamoDB version stamp.

Why: `ProviderAdapter.is_connected` runs a ~3s token-vault check (Secrets Manager read +
proactive OAuth refresh). Re-running it every turn is wasteful, but a plain in-process cache
goes STALE across Lambda containers — connect on container A leaves B serving an old answer
(it never saw the invalidation).

Design — version stamp (local cache + one shared signal):
  * DynamoDB holds ONE source of truth per user: a monotonic `v`, bumped on every
    connect/disconnect. That bump is the GLOBAL invalidation signal.
  * Each container keeps a fast in-process result cache tagged with the `v` it was computed
    under, plus a tiny short-TTL cache of `v` itself so we read DDB at most ~once per
    `_VERSION_TTL_S` per user (not per call).
  * is_connected → read current `v` (usually from the local v-cache); if the cached result
    carries the SAME `v`, reuse it (no vault check); otherwise do the real check and re-tag.
  * connect/disconnect → `invalidate()` bumps `v` in DDB AND locally, so the acting container
    is correct instantly and every other container converges within `_VERSION_TTL_S`.

Scales: reads/writes are O(1) DynamoDB point ops on a PAY_PER_REQUEST table; the hot path is
served from process memory and touches DDB only ~once per few seconds per user per container.

Degrades safely: with no table configured (local dev) or on any DDB error we fall back to a
process-local cache with a safety TTL — never breaking the connection gate. In-process
connect/disconnect still invalidates correctly (a single local process needs no shared signal).
"""

from __future__ import annotations

import os
import threading
import time

# DynamoDB table holding the per-user version stamp (set by CDK in prod; unset locally → the
# safe process-local fallback below). Region comes from the standard AWS_* env, like token_store.
_TABLE_NAME = os.environ.get("MCP_CONN_TABLE", "")
# How long a container trusts its last-read version before re-reading DDB. This bounds
# cross-container propagation of a connect/disconnect (0 = read every call, strictly fresh).
_VERSION_TTL_S = float(os.environ.get("MCP_CONN_VERSION_TTL_S", "5"))
# Safety TTL used ONLY when DDB is unavailable, so a fallback entry can't be trusted forever.
_FALLBACK_TTL_S = float(os.environ.get("MCP_CONN_FALLBACK_TTL_S", "60"))

_lock = threading.Lock()
# (user_id, mcp_url) -> (connected, version_at_compute, stored_at_monotonic)
_results: dict[tuple[str, str], tuple[bool, int, float]] = {}
# user_id -> (version, fetched_at_monotonic)
_versions: dict[str, tuple[int, float]] = {}

_table_handle = None  # lazily-created boto3 Table, reused across calls


def _table():
    """The DynamoDB Table handle, or None when caching has no shared backend (local dev)."""
    global _table_handle
    if not _TABLE_NAME:
        return None
    if _table_handle is None:
        try:
            import boto3

            _table_handle = boto3.resource("dynamodb").Table(_TABLE_NAME)
        except Exception:
            return None
    return _table_handle


def _current_version(user_id: str | None) -> int | None:
    """Per-user invalidation version, served from the short-TTL local cache and refreshed from
    DDB on expiry. Returns None when no shared backend is available (caller falls back to TTL)."""
    uid = user_id or ""
    now = time.monotonic()
    with _lock:
        hit = _versions.get(uid)
        if hit is not None and now - hit[1] <= _VERSION_TTL_S:
            return hit[0]
    table = _table()
    if table is None:
        return None
    try:
        resp = table.get_item(Key={"user_id": uid}, ProjectionExpression="v")
        version = int(resp.get("Item", {}).get("v", 0))
    except Exception:
        return None  # DDB hiccup → behave as "no shared backend" for this call
    with _lock:
        _versions[uid] = (version, now)
    return version


def get(user_id: str | None, mcp_url: str) -> bool | None:
    """Cached connection answer, or None on miss/stale (caller does the real vault check)."""
    key = (user_id or "", mcp_url)
    version = _current_version(user_id)
    with _lock:
        entry = _results.get(key)
    if entry is None:
        return None
    connected, entry_version, stored_at = entry
    if version is not None:
        # Version-stamp mode: trust the entry iff it was computed under the current version.
        return connected if entry_version == version else None
    # No shared backend → bound trust by a short safety TTL.
    return connected if time.monotonic() - stored_at <= _FALLBACK_TTL_S else None


def set(user_id: str | None, mcp_url: str, connected: bool) -> None:
    """Record a freshly-computed result, tagged with the user's current version."""
    version = _current_version(user_id) or 0
    with _lock:
        _results[(user_id or "", mcp_url)] = (connected, version, time.monotonic())


def invalidate(user_id: str | None, mcp_url: str | None = None) -> None:
    """Global invalidation — call on every connect/disconnect.

    Atomically bumps the user's version in DDB (so all containers re-check) and updates this
    container's local state immediately. `mcp_url` is accepted for call-site clarity but a bump
    invalidates ALL of the user's providers (a user has only a few; re-checking is cheap)."""
    uid = user_id or ""
    table = _table()
    new_version: int | None = None
    if table is not None:
        try:
            resp = table.update_item(
                Key={"user_id": uid},
                UpdateExpression="ADD v :one",
                ExpressionAttributeValues={":one": 1},
                ReturnValues="UPDATED_NEW",
            )
            new_version = int(resp["Attributes"]["v"])
        except Exception:
            new_version = None
    with _lock:
        if new_version is not None:
            _versions[uid] = (new_version, time.monotonic())
        else:
            _versions.pop(uid, None)  # force a fresh read next time
        # Drop this user's cached results so the ACTING container re-checks immediately.
        for key in [k for k in _results if k[0] == uid]:
            _results.pop(key, None)
