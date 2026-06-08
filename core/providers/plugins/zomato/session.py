"""Zomato session slice + merge.

The planner reuses a small LRU of `(name, res_id)` pairs it saw across the
conversation (`session_restaurants`) to resolve "order from the second one" /
"that Domino's" without re-searching. This ports the legacy executor's
seed+new merge (most-recent first, capped) onto clean `ToolResult`s.

The flat `session_restaurants` key (not a nested provider slice) is kept so the
frontend echo + the legacy planner read it unchanged during migration.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from core.contracts.session import ProviderSessionState
from core.contracts.tool_result import ToolResult
from core.providers.plugins.zomato.cards import restaurant_entries

# Max remembered restaurants — matches the executor's _MAX_REMEMBERED_RESTAURANTS
# and the card cap so the LRU, the visible cards, and narration always agree.
_MAX_REMEMBERED = 8
# Lean projection stored in session (the planner needs id+name; the rest is for
# the next-turn context line). Mirrors the legacy executor's session shape.
_SESSION_FIELDS = ("res_id", "name", "rating", "eta", "distance_km", "serviceability_status")
_SEARCH_TOOLS = {"get_restaurants_for_keyword"}


class ZomatoSession(ProviderSessionState):
    """Zomato's namespaced session slice (restaurant LRU; menu/cart added as ported)."""

    restaurants: list[dict[str, Any]] = Field(default_factory=list)


def _lean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in _SESSION_FIELDS if k in entry}


def merge_restaurants(
    prior: list[dict[str, Any]] | None, results: list[ToolResult]
) -> list[dict[str, Any]]:
    """Most-recent-first LRU of restaurants: this turn's hits, then prior, deduped, capped."""
    fresh: list[dict[str, Any]] = []
    for tr in results:
        if tr.tool not in _SEARCH_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        fresh.extend(_lean(e) for e in restaurant_entries(envelope))
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in [*fresh, *(prior or [])]:
        rid = str(entry.get("res_id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not rid or not name or rid in seen:
            continue
        seen.add(rid)
        merged.append({**entry, "res_id": rid, "name": name})
        if len(merged) >= _MAX_REMEMBERED:
            break
    return merged
