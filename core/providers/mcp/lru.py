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


def backfill_fields(*fields: str) -> Callable[[dict[str, Any], dict[str, Any]], None]:
    """Combiner for `scoped_lru_merge`: copy `fields` a kept row is missing from
    the duplicate it replaces, so a sparse re-fetch (e.g. a category listing
    without prices) never downgrades data already learned."""

    def combine(kept: dict[str, Any], dropped: dict[str, Any]) -> None:
        for field in fields:
            if kept.get(field) in (None, "", []) and dropped.get(field) not in (None, "", []):
                kept[field] = dropped[field]

    return combine


def scoped_lru_merge(
    prior: list[dict[str, Any]] | None,
    fresh: list[dict[str, Any]],
    *,
    provider: str,
    key: Callable[[dict[str, Any]], str],
    cap: int,
    combine: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Provider-scoped LRU merge for session lists shared across providers.

    Fresh rows are stamped with `provider`; dedup and `cap` apply only within
    this provider's rows, and other providers' rows pass through untouched —
    one platform's fresh page can never evict another's remembered rows, and
    every row stays attributable. Untagged prior rows (pre-scoping sessions)
    are adopted as this provider's. `combine(kept, dropped)` lets a duplicate's
    richer fields backfill the row that supersedes it."""
    merged: list[dict[str, Any]] = []
    kept_by_key: dict[str, dict[str, Any]] = {}
    count = 0
    stamped = [{**e, "provider": provider} for e in fresh if isinstance(e, dict)]
    for entry in [*stamped, *(prior or [])]:
        if not isinstance(entry, dict):
            continue
        owner = str(entry.get("provider") or provider)
        if owner != provider:
            merged.append(entry)
            continue
        k = str(key(entry) or "").strip()
        if not k:
            continue
        kept = kept_by_key.get(k)
        if kept is not None:
            if combine:
                combine(kept, entry)
            continue
        if count >= cap:
            continue
        row = {**entry, "provider": provider}
        kept_by_key[k] = row
        merged.append(row)
        count += 1
    return merged
