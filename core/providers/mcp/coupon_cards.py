"""Coupon / offer card builder (`coupon_cards`) — alias walk over any
provider's coupons/offers envelope (Swiggy fetch/apply coupons, Zomato cart
offers); shares the envelope-walk helpers with `result_cards`."""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.mcp.result_cards import _first, _walk

_MAX_COUPONS = 8

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
