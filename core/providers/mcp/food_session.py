"""Shared restaurant-LRU session merge for the food providers.

The planner reuses a small most-recent-first LRU of `(name, res_id)` pairs it has
seen to resolve "order from the second one" / "that Domino's" without re-searching.
Zomato and Swiggy share this merge; only the per-provider extraction of fresh
entries from a turn's `ToolResult`s differs (their card modules supply that).
"""

from __future__ import annotations

from typing import Any

# Max remembered restaurants — matches the executors' cap and the card cap so the
# LRU, the visible cards, and narration always agree.
MAX_REMEMBERED = 8
# Lean projection stored in session: the planner needs id+name; the rest is for
# the next-turn context line. Mirrors the legacy executors' session shape.
_SESSION_FIELDS = ("res_id", "name", "rating", "eta", "distance_km", "serviceability_status")


def lean(entry: dict[str, Any]) -> dict[str, Any]:
    """Trim a rich card entry to the planner-facing session fields."""
    return {k: entry[k] for k in _SESSION_FIELDS if k in entry}


def lru_merge(
    prior: list[dict[str, Any]] | None, fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Most-recent-first LRU: this turn's hits, then prior, deduped by res_id, capped."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in [*fresh, *(prior or [])]:
        rid = str(entry.get("res_id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not rid or not name or rid in seen:
            continue
        seen.add(rid)
        merged.append({**entry, "res_id": rid, "name": name})
        if len(merged) >= MAX_REMEMBERED:
            break
    return merged
