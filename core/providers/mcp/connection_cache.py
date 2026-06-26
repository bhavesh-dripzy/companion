"""Tiny in-process cache for "is this user's MCP provider connected?".

The source of truth stays the token vault (Secrets Manager → on-disk tokens). This only
MEMOIZES the answer so the grocery fan-out gate doesn't re-read the vault on every turn
(`ProviderAdapter.is_connected`). Two invalidation paths keep it honest:

  * explicit — `invalidate(user_id)` fires the moment a provider is connected/disconnected
    (hooked into `merge_mcp_token_secret`, the one primitive every connect/disconnect writes
    through), so the next turn in the same process sees the new status instantly;
  * TTL — a short safety net for changes no event covers (a token expiring with no refresh)
    and for cross-process staleness (another Lambda container did the connect/disconnect).

Keyed by (user_id, mcp_url). Process-local by design — cheap, no extra infra; the TTL bounds
how long a stale entry can live, and the explicit hook covers the common same-process case.
"""

from __future__ import annotations

import os
import threading
import time

# How long a cached answer is trusted without re-checking the vault. Override with
# MCP_CONN_CACHE_TTL_S (0 disables caching entirely).
_TTL_S = float(os.environ.get("MCP_CONN_CACHE_TTL_S", "180"))

_lock = threading.Lock()
# (user_id, mcp_url) -> (connected, stored_at_monotonic)
_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def get(user_id: str | None, mcp_url: str) -> bool | None:
    """Cached connection answer, or None on miss/expiry (caller does the real check)."""
    if _TTL_S <= 0:
        return None
    key = (user_id or "", mcp_url)
    with _lock:
        hit = _cache.get(key)
    if hit is None:
        return None
    connected, stored_at = hit
    if time.monotonic() - stored_at > _TTL_S:
        return None
    return connected


def set(user_id: str | None, mcp_url: str, connected: bool) -> None:
    if _TTL_S <= 0:
        return
    with _lock:
        _cache[(user_id or "", mcp_url)] = (connected, time.monotonic())


def invalidate(user_id: str | None, mcp_url: str | None = None) -> None:
    """Drop cached status. With no mcp_url, drop EVERY provider for the user (used on any
    connect/disconnect, where the exact url isn't always handy)."""
    uid = user_id or ""
    with _lock:
        if mcp_url is not None:
            _cache.pop((uid, mcp_url), None)
        else:
            for key in [k for k in _cache if k[0] == uid]:
                _cache.pop(key, None)
