"""Shared order-history and live-tracking card builders.

Every MCP exposes order-history/tracking/coupon tools whose envelopes differ
only in field spelling (validated against real envelopes: Zomato
`order_history_items[]`, Zepto `orders[]` with paise amounts, Instamart
`orders[]`). One alias-walk extractor covers them all; provider wiring supplies
the tool names, brand id, and currency quirk. Coupon and generic-info tiles
live in `coupon_cards.py` / `info_cards.py` (same package).
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_MAX_ORDERS = 8
_MAX_ITEMS_PER_ORDER = 4

_ORDER_LIST_KEYS = ("order_history_items", "orders", "order_items", "past_orders")
_ITEM_LIST_KEYS = ("catalogue_items", "productsNamesAndCounts", "items", "products")
_ID_KEYS = ("order_id", "orderId", "id", "code")
_STATUS_KEYS = ("payment_status", "formattedStatus", "order_status", "orderStatus", "status")
_PLACE_KEYS = ("res_name", "restaurant_name", "restaurantName", "store_name", "storeName")
_DATE_KEYS = ("payment_date", "order_date", "orderDate", "created_at", "createdAt", "date")
_TOTAL_TEXT_KEYS = ("payment_amount", "total_text", "amount_text")
_TOTAL_NUM_KEYS = ("grand_total", "grandTotal", "total_amount", "totalAmount", "total", "amount")
_ETA_KEYS = ("res_ddt", "eta", "deliveryTime", "delivery_time", "arrival_time")


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _order_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _ITEM_LIST_KEYS:
        raw = row.get(key)
        if not isinstance(raw, list):
            continue
        items = []
        for it in raw[:_MAX_ITEMS_PER_ORDER]:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or it.get("item_name") or "").strip()
            if not name:
                continue
            qty = it.get("quantity") or it.get("count")
            entry: dict[str, Any] = {"name": name}
            if isinstance(qty, int | float) and qty > 0:
                entry["quantity"] = int(qty)
            items.append(entry)
        if items:
            extra = len(raw) - len(items)
            return items + ([{"name": f"+{extra} more"}] if extra > 0 else [])
    return []


def order_entries(envelope: dict[str, Any], *, paise: bool = False) -> list[dict[str, Any]]:
    """Normalized order rows from any provider's order-history envelope."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return []
    rows: list[Any] = []
    for obj in _walk(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        for key in _ORDER_LIST_KEYS:
            if isinstance(obj.get(key), list):
                rows = obj[key]
                break
        if rows:
            break
    out: list[dict[str, Any]] = []
    for row in rows[:_MAX_ORDERS]:
        if not isinstance(row, dict):
            continue
        oid = _first(row, _ID_KEYS)
        if oid is None:
            continue
        entry: dict[str, Any] = {"order_id": str(oid)}
        status = _first(row, _STATUS_KEYS)
        if status:
            entry["status"] = str(status)
        place = _first(row, _PLACE_KEYS)
        if place:
            entry["place_name"] = str(place)
        date = _first(row, _DATE_KEYS)
        if date:
            entry["date"] = str(date)
        total_text = _first(row, _TOTAL_TEXT_KEYS)
        if total_text:
            entry["total_text"] = str(total_text)
        else:
            total = _first(row, _TOTAL_NUM_KEYS)
            if total is None:
                total = row.get("grandTotalAmount")
            if isinstance(total, int | float) and total > 0:
                entry["total"] = round(total / 100, 2) if paise else total
        eta = _first(row, _ETA_KEYS)
        if eta:
            entry["eta"] = str(eta)
        items = _order_items(row)
        if items:
            entry["items"] = items
        out.append(entry)
    return out


def build_order_cards(
    results: list[ToolResult],
    *,
    tools: set[str],
    provider_id: str,
    brand: str,
    paise: bool = False,
) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in tools or not tr.ok:
            continue
        entries = order_entries(tr.data if isinstance(tr.data, dict) else {}, paise=paise)
        if entries:
            out.append(
                Attachment(
                    kind="order_cards",
                    provider=brand,
                    source=f"{provider_id}.{tr.tool}",
                    past_orders=entries,
                )
            )
    return out


_TRACK_STATUS_KEYS = ("order_status", "orderStatus", "delivery_status", "status", "state")
_TRACK_ETA_KEYS = ("eta", "eta_text", "arrival_time", "expected_delivery_time", "deliveryTime")
_TRACK_STAGE_KEYS = ("stage", "current_stage", "tracking_state", "subtitle", "message")


def tracking_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """One live-order status block from a tracking envelope (None when idle)."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    snap: dict[str, Any] = {}
    for obj in _walk(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        for field, keys in (
            ("status", _TRACK_STATUS_KEYS),
            ("eta", _TRACK_ETA_KEYS),
            ("stage", _TRACK_STAGE_KEYS),
            ("order_id", _ID_KEYS),
            ("place_name", _PLACE_KEYS),
        ):
            if field not in snap:
                value = _first(obj, keys)
                if value not in (None, ""):
                    snap[field] = str(value)
    return snap if "status" in snap or "stage" in snap else None


def build_tracking_card(
    results: list[ToolResult], *, tools: set[str], provider_id: str, brand: str
) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in tools or not tr.ok:
            continue
        snap = tracking_snapshot(tr.data if isinstance(tr.data, dict) else {})
        if snap:
            out.append(
                Attachment(
                    kind="tracking_card",
                    provider=brand,
                    source=f"{provider_id}.{tr.tool}",
                    tracking=snap,
                )
            )
    return out
