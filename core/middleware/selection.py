"""Resolve a natural-language entity switch ("deliver to Work") to a committed id.

Generic across any provider that declares `Selectable`s: when the user's message
carries a switch cue AND names a saved entity by its short tag/label, commit that
entity's id to the provider's session key. Deterministic and conservative — it
fires only on an explicit switch phrase plus a whole-word label match, so it never
hijacks an ordinary request. The committed id then drives the provider's before_run
anchor and round-trips to the client, so the switch persists across turns.
"""

from __future__ import annotations

import re
from typing import Any

from core.contracts.selectable import Selectable

_SWITCH_CUE = re.compile(r"\b(switch\w*|change\w*|deliver\w*|send|ship|set|use)\b")


def _candidates(row: dict[str, Any], sel: Selectable) -> list[str]:
    out: list[str] = []
    for field in sel.label_fields:
        value = str(row.get(field) or "").strip()
        if value:
            out.append(value.split(" — ")[0].strip())
    return out


def _best_match(text: str, rows: list[Any], sel: Selectable) -> str | None:
    """The id of the saved row whose longest tag/label appears whole-word in text."""
    best_id, best_len = None, 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get(sel.id_field) or "").strip()
        if not rid:
            continue
        for cand in _candidates(row, sel):
            low = cand.lower()
            if len(low) >= 3 and len(low) > best_len and re.search(rf"\b{re.escape(low)}\b", text):
                best_id, best_len = rid, len(low)
    return best_id


def resolve_selections(
    message: str, legacy: dict[str, Any], selectables: list[Selectable]
) -> dict[str, str]:
    """Map committed_key -> id for every selectable the user explicitly switched to."""
    text = (message or "").lower()
    if not _SWITCH_CUE.search(text):
        return {}
    commits: dict[str, str] = {}
    for sel in selectables:
        rows = legacy.get(sel.saved_key)
        if not isinstance(rows, list):
            continue
        match = _best_match(text, rows, sel)
        if match:
            commits[sel.committed_key] = match
    return commits
