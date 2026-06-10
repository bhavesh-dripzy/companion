"""Shared card builders for the long-tail read tools (orders, tracking,
coupons) plus the generic `info_card` fallback.

Every MCP exposes order-history/tracking/coupon tools whose envelopes differ
only in field spelling (validated against real envelopes: Zomato
`order_history_items[]`, Zepto `orders[]` with paise amounts, Instamart
`orders[]`). One alias-walk extractor covers them all; provider wiring supplies
the tool names, brand id, and currency quirk. `info_cards` guarantees ANY other
successful result still renders a tile — new tools get one for free.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_MAX_ORDERS = 8
_MAX_ITEMS_PER_ORDER = 4
_MAX_COUPONS = 8
_MAX_INFO_ROWS = 10
_MAX_INFO_LINES = 8

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


_COUPON_LIST_KEYS = ("coupons", "offers", "coupon_list", "available_coupons", "vouchers")
_COUPON_CODE_KEYS = ("code", "coupon_code", "couponCode", "offer_code")
_COUPON_TITLE_KEYS = ("title", "header", "name", "headline")
_COUPON_DESC_KEYS = ("description", "subtitle", "details", "terms")
_COUPON_SAVE_KEYS = ("savings_text", "discount_text", "savings", "discount", "offer_amount")


def coupon_entries(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized coupon rows from a coupons/offers envelope."""
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for obj in _walk(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        for key in _COUPON_LIST_KEYS:
            raw = obj.get(key)
            if not isinstance(raw, list):
                continue
            for row in raw:
                if not isinstance(row, dict) or len(out) >= _MAX_COUPONS:
                    continue
                code = _first(row, _COUPON_CODE_KEYS)
                title = _first(row, _COUPON_TITLE_KEYS)
                if code is None and title is None:
                    continue
                dedup = str(code or title)
                if dedup in seen:
                    continue
                seen.add(dedup)
                entry: dict[str, Any] = {}
                if code:
                    entry["code"] = str(code)
                if title:
                    entry["title"] = str(title)
                desc = _first(row, _COUPON_DESC_KEYS)
                if desc:
                    entry["description"] = str(desc)[:140]
                save = _first(row, _COUPON_SAVE_KEYS)
                if save not in (None, ""):
                    entry["savings_text"] = str(save)
                out.append(entry)
    return out


def build_coupon_cards(
    results: list[ToolResult], *, tools: set[str], provider_id: str, brand: str
) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in tools or not tr.ok:
            continue
        entries = coupon_entries(tr.data if isinstance(tr.data, dict) else {})
        if entries:
            out.append(
                Attachment(
                    kind="coupon_cards",
                    provider=brand,
                    source=f"{provider_id}.{tr.tool}",
                    coupons=entries,
                )
            )
    return out


def _prettify(name: str) -> str:
    return name.replace("_", " ").strip().title()


def info_card_payload(tool: str, envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Generic titled key-value tile from any envelope (the catch-all renderer)."""
    from core.providers.mcp.digest import lean_digest

    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict) or envelope.get("is_error"):
        return None
    rows: list[dict[str, str]] = []
    for key, value in data.items():
        if isinstance(value, str | int | float | bool) and value not in ("", None):
            text = str(value)
            if text.startswith(("http://", "https://")) or len(text) > 160:
                continue
            rows.append({"label": _prettify(str(key)), "value": text})
        if len(rows) >= _MAX_INFO_ROWS:
            break
    lines: list[str] = []
    if not rows:
        digest = lean_digest(envelope)
        if digest:
            lines = digest.splitlines()[:_MAX_INFO_LINES]
    if not rows and not lines:
        return None
    payload: dict[str, Any] = {"title": _prettify(tool), "rows": rows}
    if lines:
        payload["lines"] = lines
    return payload


def build_info_cards(
    results: list[ToolResult],
    *,
    exclude: set[str],
    provider_id: str,
    brand: str,
) -> list[Attachment]:
    """`info_card` for every ok result not already covered by a bespoke card —
    the guarantee that no successful tool result is invisible in the UI."""
    out: list[Attachment] = []
    for tr in results:
        if tr.tool in exclude or not tr.ok:
            continue
        payload = info_card_payload(tr.tool, tr.data if isinstance(tr.data, dict) else {})
        if payload:
            out.append(
                Attachment(
                    kind="info_card",
                    provider=brand,
                    source=f"{provider_id}.{tr.tool}",
                    info=payload,
                )
            )
    return out
