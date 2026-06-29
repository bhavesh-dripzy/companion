"""SerpApi HTTP client with TTL caching and a daily cost guard.

Every SerpApi request is billed, so identical lookups within a short window are served from an
in-process TTL cache, and a soft per-day request budget fails fast instead of silently burning
credits. The key (`SERPAPI_KEY`) is read from env or the app secret and never enters the cache key.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

_BASE_URL = "https://serpapi.com/search.json"
_TIMEOUT_SECONDS = 45.0
_CACHE_MAX_ENTRIES = 256

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_budget_day: str = ""
_budget_spent: int = 0


class SerpBudgetExceeded(Exception):
    """Raised when the soft daily request budget is exhausted."""


def _api_key() -> str:
    key = (os.getenv("SERPAPI_KEY") or "").strip()
    if key:
        return key
    try:
        from companion_api.secrets import secret_or_env

        return secret_or_env("SERPAPI_KEY")
    except Exception:
        return ""


def _cache_ttl() -> int:
    try:
        return max(0, int(os.getenv("SERPAPI_CACHE_TTL_SECONDS", "300")))
    except ValueError:
        return 300


def _daily_budget() -> int:
    try:
        return max(1, int(os.getenv("SERPAPI_DAILY_BUDGET", "250")))
    except ValueError:
        return 250


def _cache_key(params: dict[str, Any]) -> str:
    return "&".join(f"{k}={params[k]}" for k in sorted(params))


def _take_budget() -> None:
    global _budget_day, _budget_spent
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if today != _budget_day:
        _budget_day, _budget_spent = today, 0
    if _budget_spent >= _daily_budget():
        raise SerpBudgetExceeded(
            f"SerpApi daily budget ({_daily_budget()} requests) exhausted; "
            "raise SERPAPI_DAILY_BUDGET or wait for the UTC day to roll over"
        )
    _budget_spent += 1


def reset_for_tests() -> None:
    global _budget_day, _budget_spent
    _cache.clear()
    _budget_day, _budget_spent = "", 0


async def search(params: dict[str, Any]) -> dict[str, Any]:
    """One SerpApi search (cached). `params` must include `engine`; the api_key is injected
    here and never participates in the cache key."""
    key = _cache_key(params)
    ttl = _cache_ttl()
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    api_key = _api_key()
    if not api_key:
        raise RuntimeError("SERPAPI_KEY is not configured (env or app secret)")
    _take_budget()

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(_BASE_URL, params={**params, "api_key": api_key})
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        message = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(message or f"SerpApi HTTP {response.status_code}")
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))

    if ttl > 0:
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[key] = (now + ttl, payload)
    return payload
