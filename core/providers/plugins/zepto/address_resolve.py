"""Deterministic resolution of a planner-built Zepto `select_saved_address`.

The planner is given the saved addresses with their literal ids but routinely
emits a fake like `office_address_id`, a placeholder, or the label itself instead
of the real addressId — Zepto then rejects it ("address not found") and the
narrator wrongly tells the user that address isn't saved. This maps the supplied
arg (or the label named in the user message) to a REAL saved-address id by
tag/label match, so "deliver to office" switches to the office address. Pure
function of the session bag; no I/O.
"""

from __future__ import annotations

import re
from typing import Any

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "my",
        "to",
        "address",
        "deliver",
        "delivery",
        "change",
        "switch",
        "set",
        "use",
        "instead",
        "id",
        "zepto",
        "for",
        "of",
        "place",
    }
)


def _tokens(text: Any) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if t not in _STOP and len(t) > 1
    }


def saved_address_ids(session: dict[str, Any]) -> set[str]:
    rows = session.get("session_zepto_addresses") if isinstance(session, dict) else None
    return {
        str(r.get("address_id")).strip()
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("address_id")
    }


def resolve_address_id(supplied: str, message: str, session: dict[str, Any]) -> str | None:
    """The real saved-address id the user means, or None when undecidable.

    A supplied value that's already a real id wins. Otherwise match the label/tag
    against the supplied arg (e.g. `office_address_id` -> "office") and the user
    message; require an unambiguous single match so we never silently switch to
    the wrong address."""
    real = saved_address_ids(session)
    supplied = str(supplied or "").strip()
    if supplied in real:
        return supplied
    rows = session.get("session_zepto_addresses") if isinstance(session, dict) else None
    want = _tokens(supplied) | _tokens(message)
    if not want or not isinstance(rows, list):
        return None
    hits: list[str] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("address_id"):
            continue
        label = str(r.get("label") or r.get("tag") or "")
        if _tokens(label) & want:
            hits.append(str(r["address_id"]).strip())
    return hits[0] if len(set(hits)) == 1 else None
