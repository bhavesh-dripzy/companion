"""Shared restaurant-LRU session merge for the food providers.

The planner reuses a small most-recent-first LRU of `(name, res_id)` pairs it has
seen to resolve "order from the second one" / "that Domino's" without re-searching.
Zomato and Swiggy share this merge; only the per-provider extraction of fresh
entries from a turn's `ToolResult`s differs (their card modules supply that).
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.lru import scoped_lru_merge

# Max remembered restaurants PER PROVIDER — matches the executors' cap and the
# card cap so the LRU, the visible cards, and narration always agree.
MAX_REMEMBERED = 8
# Lean projection stored in session: the planner needs id+name; the rest is for
# the next-turn context line. Mirrors the legacy executors' session shape.
_SESSION_FIELDS = ("res_id", "name", "rating", "eta", "distance_km", "serviceability_status")


def lean(entry: dict[str, Any]) -> dict[str, Any]:
    """Trim a rich card entry to the planner-facing session fields."""
    return {k: entry[k] for k in _SESSION_FIELDS if k in entry}


def _valid(entry: dict[str, Any]) -> bool:
    return bool(str(entry.get("res_id") or "").strip() and str(entry.get("name") or "").strip())


def lru_merge(
    prior: list[dict[str, Any]] | None, fresh: list[dict[str, Any]], *, provider: str
) -> list[dict[str, Any]]:
    """Most-recent-first restaurant LRU, scoped per provider (`scoped_lru_merge`)."""
    return scoped_lru_merge(
        [e for e in (prior or []) if isinstance(e, dict) and _valid(e)],
        [e for e in fresh if isinstance(e, dict) and _valid(e)],
        provider=provider,
        key=lambda e: str(e.get("res_id") or "").strip(),
        cap=MAX_REMEMBERED,
    )
