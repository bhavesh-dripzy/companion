"""District session merge — per-entity LRUs (restaurants/movies/events/...).

Each discovery spec with a `session_key` feeds an LRU the planner reads next turn
to resolve "book the second one" / "that theatre" without re-searching. Reuses the
card builders (via `cards_for`) so the session entries match the rendered cards.
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool_result import ToolResult
from core.providers.mcp.lru import lru_merge
from core.providers.plugins.district.cards import SPECS, cards_for

_MAX_REMEMBERED = 12
_BY_TOOL = {s.tool: s for s in SPECS if s.session_key}


def merge(slice_: dict[str, Any], results: list[ToolResult]) -> dict[str, Any]:
    """Fold this turn's discovery results into each entity's session LRU."""
    merged = dict(slice_)
    # tool -> fresh cards this turn (grouped by the session key they feed).
    fresh_by_key: dict[str, list[dict[str, Any]]] = {}
    id_key_by_key: dict[str, str] = {}
    for tr in results:
        spec = _BY_TOOL.get(tr.tool)
        if spec is None or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        fresh_by_key.setdefault(spec.session_key, []).extend(cards_for(spec, envelope))
        id_key_by_key[spec.session_key] = spec.id_key
    for session_key, fresh in fresh_by_key.items():
        if not fresh:
            continue
        id_key = id_key_by_key[session_key]
        merged[session_key] = lru_merge(
            slice_.get(session_key), fresh, key=lambda e, k=id_key: e.get(k), cap=_MAX_REMEMBERED
        )
    return merged
