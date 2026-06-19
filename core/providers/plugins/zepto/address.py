"""Parse Zepto's prose ``list_saved_addresses`` reply into address rows.

Unlike Instamart's structured ``get_addresses``, Zepto returns prose: a numbered
body (``N. <label>: <line>``) plus a footer mapping the SAME numbers to ids
(``N. "<label>" → ID: <uuid>``). We pair body↔footer by their shared index and
emit rows shaped like Instamart's so the rest of the address machinery (cards,
picker, anchor) is provider-agnostic.
"""

from __future__ import annotations

import re
from typing import Any

# Body:   `1. work: hotel maharaja, 652, Sector 38 Rd, …`
_BODY_RE = re.compile(r"^\s*(\d+)\.\s*([^:]+?):\s*(.+?)\s*$", re.MULTILINE)
# Footer: `5. "work" → ID: dc05eadc-…`  (→ may be rendered as -> in some clients)
_FOOTER_RE = re.compile(
    r'^\s*(\d+)\.\s*"([^"]+)"\s*(?:→|->)\s*ID:\s*([0-9a-fA-F-]{36})\s*$', re.MULTILINE
)


def _text(envelope: dict[str, Any]) -> str:
    """Best-effort prose extraction across the envelope shapes Zepto replies use:
    a bare string, ``{summary|raw_text|text: "…"}``, or a nested ``{data: …}``."""
    data: Any = envelope
    for _ in range(3):  # unwrap up to a few levels of {"data": …}
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("summary", "raw_text", "text"):
                if isinstance(data.get(key), str) and data[key].strip():
                    return data[key]
            if "data" in data:
                data = data["data"]
                continue
        break
    return ""


def extract_zepto_address_rows(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows ``[{address_id, label, tag, line}]`` from a list_saved_addresses reply.

    Joined by the shared 1-based index in the prose body and the id footer.
    Returns ``[]`` when the footer (the only id source) is absent."""
    text = _text(envelope)
    if not text:
        return []
    # The id footer (`N. "label" → ID: uuid`) ALSO matches the looser body regex,
    # so split on the "Address IDs:" marker and parse each section separately.
    head, _, foot = text.partition("Address IDs:")
    body = {int(n): (label.strip(), line.strip()) for n, label, line in _BODY_RE.findall(head)}
    ids = {int(n): (label.strip(), aid.strip()) for n, label, aid in _FOOTER_RE.findall(foot)}
    rows: list[dict[str, Any]] = []
    for n in sorted(ids):
        label, aid = ids[n]
        line = body.get(n, ("", ""))[1]
        rows.append(
            {
                "address_id": aid,
                "label": label,
                "tag": label,
                "line": line,
                "provider": "zepto",
            }
        )
    return rows


def normalize_address_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project rows to the UI shape ``{id, tag, line}`` (mirrors Instamart cards)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("address_id") or r.get("id") or "").strip()
        if not rid:
            continue
        out.append(
            {
                "id": rid,
                "tag": str(r.get("tag") or r.get("label") or "").strip(),
                "line": str(r.get("line") or r.get("address_line") or "").strip(),
            }
        )
    return out
