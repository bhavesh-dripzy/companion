"""District reservation-card builder (clean port of `_extract_reservation_snapshot`).

Lifts a `create_table_reservation_booking` / `get_booking_details` envelope into a
chat-renderable reservation snapshot (booking_id required). The booking tool's own
args supply fields the server may not echo (party_size, booking_date, slot_time).
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.plugins.district.discovery import envelope_data

_BOOKING_TOOLS = {"create_table_reservation_booking", "get_booking_details"}

# field -> (envelope aliases, kind) where kind is "str" | "int" | "float".
_STR_FIELDS = {
    "res_name": ("res_name", "restaurant_name", "name"),
    "slot_time": ("slot_time", "time"),
    "booking_date": ("booking_date", "date"),
    "status": ("booking_status", "status"),
    "payment_status": ("payment_status",),
    "confirmation_url": ("confirmation_url", "booking_url", "web_url"),
    "cancellation_url": ("cancellation_url", "cancel_url"),
    "message": ("message",),
    "res_id": ("res_id", "restaurant_id"),
}
_NUM_FIELDS = {
    "party_size": ("party_size", "guests"),
    "final_amount": ("final_amount", "amount", "total"),
}


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def reservation_snapshot(
    envelope: dict[str, Any], *, args: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    blob = envelope_data(envelope)
    bid: str | None = None
    strs: dict[str, str] = {}
    nums: dict[str, float] = {}
    for obj in _walk(blob):
        if not isinstance(obj, dict):
            continue
        if not bid:
            cand = obj.get("booking_id") or obj.get("order_id")
            if cand is not None and str(cand).strip():
                bid = str(cand).strip()
        for field, aliases in _STR_FIELDS.items():
            if field in strs:
                continue
            for alias in aliases:
                v = obj.get(alias)
                key = (
                    v.strip()
                    if isinstance(v, str) and v.strip()
                    else (str(v).strip() if field in ("res_id",) and v not in (None, "") else None)
                )
                if key:
                    strs[field] = key
                    break
        for field, aliases in _NUM_FIELDS.items():
            if field in nums:
                continue
            for alias in aliases:
                v = obj.get(alias)
                if isinstance(v, int | float) and not isinstance(v, bool):
                    nums[field] = float(v)
                    break
    if not bid:
        return None
    args = args or {}
    for field in ("slot_time", "booking_date", "res_id"):
        if field not in strs and isinstance(args.get(field), str) and args[field].strip():
            strs[field] = args[field].strip()
    if "party_size" not in nums and isinstance(args.get("party_size"), int | float):
        nums["party_size"] = float(args["party_size"])

    snap: dict[str, Any] = {"booking_id": bid}
    if "res_name" in strs:
        snap["restaurant_name"] = strs.pop("res_name")
    snap.update(strs)
    if "party_size" in nums:
        snap["party_size"] = int(nums.pop("party_size"))
    snap.update(nums)
    return snap


def build_reservation_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _BOOKING_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        snap = reservation_snapshot(envelope, args=tr.arguments)
        if snap:
            out.append(
                Attachment(
                    kind="reservation_card",
                    provider="district",
                    source=f"district.{tr.tool}",
                    reservation=snap,
                )
            )
    return out
