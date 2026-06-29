"""Generic per-domain 'result set' memory for the read-only domains (news/flights/hotels).

Stores the items last shown to the user with STABLE refs, so a follow-up that references one
("the 2nd one", "that ₹6228 flight", "Ben Stokes wali") resolves to the EXACT object — the same
ref + notepad idea grocery's SessionCatalog uses, but lightweight and domain-agnostic. Lives on
`state.serp_session[domain]`. Reusable: any read-only domain calls remember()/notepad()/by_ref().
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def remember(
    state, domain: str, items: list[Any], label: Callable[[Any], str]
) -> list[dict[str, str]]:
    """Store `items` with refs (n1, n2, … for 'news') and return the compact notepad to hand the
    brain next turn: [{"ref": "n2", "label": "<title>"}]. Code can later pin the exact item by ref."""
    prefix = (domain[:1] or "r").lower()
    by_ref: dict[str, Any] = {}
    pad: list[dict[str, str]] = []
    for i, item in enumerate(items, 1):
        ref = f"{prefix}{i}"
        by_ref[ref] = item
        pad.append({"ref": ref, "label": label(item)})
    state.serp_session[domain] = {"by_ref": by_ref, "notepad": pad}
    return pad


def notepad(state, domain: str) -> list[dict[str, str]]:
    """The [{ref, label}] list of what was shown last turn (empty if nothing yet)."""
    return (state.serp_session.get(domain) or {}).get("notepad", [])


def by_ref(state, domain: str, ref: str) -> Any:
    """The exact stored item for a ref, or None."""
    return (state.serp_session.get(domain) or {}).get("by_ref", {}).get((ref or "").strip())
