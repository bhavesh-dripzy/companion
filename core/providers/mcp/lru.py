"""Generic most-recent-first LRU merge for session memory lists.

Several providers carry a recency-bounded list across turns (restaurants,
products, addresses) so the planner can reuse an id it has already seen without
re-searching. They differ only in the dedup key and cap; this is that merge.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def lru_merge(
    prior: list[dict[str, Any]] | None,
    fresh: list[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], str],
    cap: int,
) -> list[dict[str, Any]]:
    """Fresh entries first, then prior, deduped by `key`, trimmed to `cap`."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in [*fresh, *(prior or [])]:
        if not isinstance(entry, dict):
            continue
        k = str(key(entry) or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        merged.append(entry)
        if len(merged) >= cap:
            break
    return merged
