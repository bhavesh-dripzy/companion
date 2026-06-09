"""Saved-address extraction from MCP envelopes (dual-key aware).

Ported from `zomato.mcp_executor`. Saved-address payloads spell the id three ways
(`address_id` / `addressId` / `id`) — the Swiggy dual-key parity trap — and nest
the list under `addresses` or `result.addresses`. Rows are normalized to carry
`address_id` + a friendly `label`, everything else riding along untouched.
"""

from __future__ import annotations

from typing import Any


def _addresses_list(payload: dict[str, Any]) -> list[Any]:
    blob = payload.get("data") if isinstance(payload, dict) else None
    if blob is None:
        return []
    data_dict = (
        blob if isinstance(blob, dict) else (blob[0] if isinstance(blob, list) and blob else {})
    )
    if not isinstance(data_dict, dict):
        return []
    raw = data_dict.get("addresses") or (data_dict.get("result") or {}).get("addresses")
    return raw if isinstance(raw, list) else []


def _label_for(item: dict[str, Any], address_id: str) -> str:
    tag = str(item.get("addressTag") or item.get("address_tag") or "").strip()
    line = str(
        item.get("address_line")
        or item.get("addressLine")
        or item.get("location_name")
        or item.get("formatted_address")
        or ""
    ).strip()
    if tag and line and line.lower() != tag.lower():
        return f"{tag} — {line}"
    return line or tag or address_id


def extract_address_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize saved-address rows: each carries `address_id` + `label`."""
    rows: list[dict[str, Any]] = []
    for item in _addresses_list(payload):
        if not isinstance(item, dict):
            continue
        aid = item.get("address_id") or item.get("addressId") or item.get("id")
        sid = str(aid).strip() if aid is not None else ""
        if not sid:
            continue
        rows.append({**item, "address_id": sid, "label": _label_for(item, sid)})
    return rows


def first_address_id(payload: dict[str, Any]) -> str | None:
    """The first usable saved-address id, or None."""
    rows = extract_address_rows(payload)
    return rows[0]["address_id"] if rows else None


def merge_saved_addresses(merged: dict[str, Any], results: Any) -> None:
    """Surface saved-address rows into ``merged['session_addresses']`` (in place).

    Called from a food provider's `merge` so a `get_addresses` /
    `get_saved_addresses_for_user` result reaches the frontend's address picker
    and the next turn can resolve `__ADDRESS_ID__` from `session_addresses[0]`."""
    for tr in results:
        if getattr(tr, "ok", False) and "address" in getattr(tr, "tool", "").lower():
            rows = extract_address_rows(tr.data if isinstance(tr.data, dict) else {})
            if rows:
                merged["session_addresses"] = rows
