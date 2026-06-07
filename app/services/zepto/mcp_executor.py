"""Zepto MCP tool execution via mcp-remote + stdio.

Planner-first architecture (mirrors ``zomato/mcp_executor.py``,
``swiggy/mcp_executor.py``, and ``district/mcp_executor.py``):

* The **planner LLM** is fully responsible for every literal argument —
  ``cartItems`` shape, ``query`` text, address / store ids, etc.
* The **executor** does only deterministic plumbing:

  - stdio bridge to ``mcp-remote`` with the user's OAuth tokens
  - placeholder substitution (``__USER_LAT__``, ``__USER_LNG__``,
    ``__ZEPTO_ADDRESS_ID__``, ``__ZEPTO_STORE_ID__``, ``__ZEPTO_PVID__``,
    ``__ZEPTO_SPID__``, ``__ZEPTO_ORDER_ID__``, ``__ZEPTO_DEVICE_ID__``)
  - post-call harvesting of placeholders + session indexes (products,
    addresses, cart snapshot) from Zepto's prose response bodies
  - one narrow defensive coercion: ``latitude`` / ``longitude`` strings
    coming out of placeholder substitution are converted back to floats,
    since the MCP server rejects string-typed coords.

Why text harvesting instead of dict-walking? Zepto's MCP returns
ChatGPT-Apps-style formatted prose with an ID footer per response:

    Found 10 products for "milk":
    1. Country Delight ... - ₹49 (1 pack (450 ml))
    ...
    Product IDs:
    [1] pvid: f1f1d95f-..., spid: d6733aa7-...
    [2] pvid: fdf82a8b-..., spid: 611e3811-...

We regex-parse these footers (and the JSON-inside-string blocks for
``list_order_history`` / ``get_order_detail``) so downstream tool calls
and frontend cards can render real ids without forcing the planner to
do bookkeeping.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("ZEPTO_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("ZEPTO_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "60")
)
_DEFAULT_ZEPTO_MCP_URL = "https://mcp.zepto.co.in/mcp"
_MAX_REMEMBERED = max(1, int(os.getenv("ZEPTO_REMEMBERED_CAP", "16")))

from app.services.zomato.mcp_executor import (  # noqa: E402
    _humanize_mcp_failure,
    _stdio_env,
    _tool_result_payload,
    ensure_mcp_tokens_on_disk,
    repo_root_from_settings,
    substitute_placeholders,
)

# ---------------------------------------------------------------------------
# Placeholders
# ---------------------------------------------------------------------------
# Placeholders the planner may emit. After every tool call, the executor
# walks the response and tries to refresh values for each one. The first
# non-empty match wins.
_ZEPTO_PLACEHOLDER_ALIASES: tuple[str, ...] = (
    "__USER_LAT__",
    "__USER_LNG__",
    "__ZEPTO_ADDRESS_ID__",
    "__ZEPTO_STORE_ID__",
    "__ZEPTO_PVID__",
    "__ZEPTO_SPID__",
    "__ZEPTO_ORDER_ID__",
    "__ZEPTO_DEVICE_ID__",
)

# Placeholders that survive across turns (the response LLM may reference
# them in follow-ups: "the milk we found", "the office address", etc.).
_ZEPTO_SESSION_PERSISTENT = frozenset(_ZEPTO_PLACEHOLDER_ALIASES)

# Tools that *issue* a fresh value for a placeholder. When a turn re-runs
# one of these, any value seeded from a previous turn is stale and must be
# cleared so the new response's value is re-learned.
_ZEPTO_TOOL_ISSUES_PLACEHOLDER: dict[str, tuple[str, ...]] = {
    "search_products": ("__ZEPTO_PVID__", "__ZEPTO_SPID__"),
    "search_multiple_products": ("__ZEPTO_PVID__", "__ZEPTO_SPID__"),
    "get_location_serviceability": ("__ZEPTO_STORE_ID__",),
    "list_order_history": ("__ZEPTO_ORDER_ID__",),
    "create_order": ("__ZEPTO_ORDER_ID__",),
    "create_online_payment_order": ("__ZEPTO_ORDER_ID__",),
    "create_wallet_order": ("__ZEPTO_ORDER_ID__",),
    "create_upi_reserve_pay_order": ("__ZEPTO_ORDER_ID__",),
}

# ---------------------------------------------------------------------------
# Defensive scrub: same pattern as district/mcp_executor — Zepto rejects
# string-typed coordinates, but placeholder substitution always produces
# strings. So after substitution we coerce numeric-looking lat/lng strings
# back to floats. Narrow, justified, not a transfer of LLM responsibility.
# ---------------------------------------------------------------------------
_LATLNG_KEYS = frozenset({"latitude", "longitude", "lat", "lng", "lon"})


def _coerce_latlng_floats(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _LATLNG_KEYS and isinstance(v, str):
                try:
                    out[k] = float(v.strip())
                    continue
                except (TypeError, ValueError):
                    pass
            out[k] = _coerce_latlng_floats(v)
        return out
    if isinstance(obj, list):
        return [_coerce_latlng_floats(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Search responses: ``[1] pvid: <uuid>, spid: <uuid>``
_PVID_SPID_RE = re.compile(
    rf"\[(\d+)\]\s*pvid:\s*({_UUID_RE})\s*,\s*spid:\s*({_UUID_RE})",
    re.IGNORECASE,
)
# Saved-address footer: ``1. "label" → ID: <uuid>``
_ADDR_FOOTER_RE = re.compile(
    rf'(\d+)\.\s*"([^"]+)"\s*(?:→|->)\s*ID:\s*({_UUID_RE})'
)
# Serviceability response: ``Primary store ID: <uuid>``
_PRIMARY_STORE_RE = re.compile(
    rf"Primary store ID:\s*({_UUID_RE})", re.IGNORECASE
)
# Embedded JSON block in list_order_history / get_order_detail / view_cart.
_EMBEDDED_JSON_RE = re.compile(r"\{[\s\S]*\}\s*$")


def _zepto_response_text(envelope: dict[str, Any]) -> str:
    """Pull the prose body out of a Zepto MCP envelope.

    Zepto MCP tools sometimes return ``structuredContent`` (a clean JSON
    object) and sometimes only return a single text block. The shared
    ``_tool_result_payload`` helper prefers ``structuredContent`` when
    present, so for most Zepto tools ``envelope["data"]`` is the
    structured dict, and the prose lives under a ``summary`` key (for
    ``list_saved_addresses``) or in the text block we re-parse below.

    Resolution order:
      1. ``data["raw_text"]`` (text-only block path)
      2. ``data["summary"]`` (structured envelope's prose tail)
      3. ``data`` if it's already a string
      4. Empty
    """
    data = envelope.get("data")
    if isinstance(data, dict):
        rt = data.get("raw_text")
        if isinstance(rt, str):
            return rt
        summary = data.get("summary")
        if isinstance(summary, str):
            return summary
        return ""
    if isinstance(data, str):
        return data
    return ""


def _zepto_structured(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Return the structured payload (when present) or ``None``.

    Skips the ``{"raw_text": ...}`` wrapper that the shared payload
    helper emits for text-only responses, since that's not real
    structured data.
    """
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    # Treat raw_text-only blobs as "no structured payload".
    if set(data.keys()) == {"raw_text"}:
        return None
    return data


def _zepto_is_error(envelope: dict[str, Any]) -> bool:
    if envelope.get("is_error"):
        return True
    text = _zepto_response_text(envelope)
    return text.lstrip().lower().startswith("error:")


def _extract_embedded_json(text: str) -> Any | None:
    """Pull a trailing ``{...}`` JSON block out of a Zepto prose body."""
    if not text:
        return None
    m = _EMBEDDED_JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Search-result parsing (numbered list + ID footer → product rows)
# ---------------------------------------------------------------------------
# Numbered list line, e.g.
#   ``1. Country Delight Natural Fresh Cow Milk | 25g Protein | Pouch - ₹49 (1 pack (450 ml))``
_PRODUCT_LINE_RE = re.compile(
    r"^\s*(?P<rank>\d+)\.\s+(?P<rest>.+)$",
)
# Trailing ``- ₹<price>`` (the price marker — first ₹ wins).
_PRICE_RE = re.compile(r"-\s*₹\s*([0-9,]+(?:\.\d+)?)")
# Trailing pack-size in parens at the very end of the line.
_PACK_TAIL_RE = re.compile(r"\(([^()]+(?:\([^()]*\))?)\)\s*$")
# Section header for ``search_multiple_products``:
#   ``### Results for "milk" (10 products)``
_RESULTS_SECTION_RE = re.compile(
    r"^\s*###\s+Results\s+for\s+\"([^\"]+)\"(?:\s*\((\d+)\s+products?\))?\s*$",
    re.MULTILINE,
)


def _parse_product_line(line: str) -> dict[str, Any] | None:
    """Parse a numbered product line into ``{rank, name, price, pack}``."""
    m = _PRODUCT_LINE_RE.match(line)
    if not m:
        return None
    rank = int(m.group("rank"))
    rest = m.group("rest").strip()

    # Pack-size (last parenthesised group on the line).
    pack: str | None = None
    pm = _PACK_TAIL_RE.search(rest)
    if pm:
        pack = pm.group(1).strip()
        rest = rest[: pm.start()].rstrip()

    # Price (first ``- ₹`` segment from the right). We keep this as a
    # float in rupees so downstream UI can format consistently.
    price: float | None = None
    price_text: str | None = None
    pricem = _PRICE_RE.search(rest)
    if pricem:
        price_text = f"₹{pricem.group(1)}"
        try:
            price = float(pricem.group(1).replace(",", ""))
        except ValueError:
            price = None
        rest = rest[: pricem.start()].rstrip(" -")

    return {
        "rank": rank,
        "name": rest.strip(),
        "price_inr": price,
        "price_text": price_text,
        "pack_size": pack,
    }


def _parse_id_footer(text: str) -> dict[int, tuple[str, str]]:
    """Return ``{rank: (pvid, spid)}`` from a Zepto search ID footer."""
    out: dict[int, tuple[str, str]] = {}
    for m in _PVID_SPID_RE.finditer(text):
        try:
            rank = int(m.group(1))
        except ValueError:
            continue
        out[rank] = (m.group(2), m.group(3))
    return out


def _product_from_structured(item: dict[str, Any], *, query: str | None = None) -> dict[str, Any] | None:
    """Normalise one structured Zepto product row into a planner-friendly dict."""
    pvid = (
        item.get("productVariantId")
        or item.get("product_variant_id")
        or item.get("variantId")
        or item.get("id")
    )
    spid = item.get("storeProductId") or item.get("store_product_id")
    name = item.get("name")
    if not isinstance(pvid, str) or not pvid.strip():
        return None
    if not isinstance(spid, str) or not spid.strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    # Zepto prices are in paise (₹49 → 4900); normalise to integer rupees
    # so the UI can format consistently with the other providers.
    price_paise = item.get("price")
    mrp_paise = item.get("mrp")
    price_inr: float | None = None
    mrp_inr: float | None = None
    if isinstance(price_paise, (int, float)):
        price_inr = round(float(price_paise) / 100, 2)
    if isinstance(mrp_paise, (int, float)):
        mrp_inr = round(float(mrp_paise) / 100, 2)
    out: dict[str, Any] = {
        "product_variant_id": pvid.strip(),
        "store_product_id": spid.strip(),
        "name": name.strip(),
        "price_inr": price_inr,
        "price_text": f"₹{price_inr:g}" if price_inr is not None else None,
        "mrp_inr": mrp_inr,
        "pack_size": item.get("packSize") or item.get("pack_size") or item.get("unit"),
        "image_url": item.get("imageUrl") or item.get("image_url"),
        "available_quantity": item.get("availableQuantity")
        or item.get("available_quantity"),
        "is_ad": bool(item.get("isAd") or item.get("is_ad")),
        "query": query,
    }
    return out


def _products_from_text(
    text: str, *, query: str | None = None
) -> list[dict[str, Any]]:
    """Text-fallback harvester for ``search_products``-style prose."""
    if not text:
        return []
    ids = _parse_id_footer(text)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parsed = _parse_product_line(line)
        if not parsed:
            continue
        rank = parsed["rank"]
        pair = ids.get(rank)
        if not pair:
            continue
        pvid, spid = pair
        rows.append(
            {
                "product_variant_id": pvid,
                "store_product_id": spid,
                "name": parsed["name"],
                "price_inr": parsed["price_inr"],
                "price_text": parsed["price_text"],
                "mrp_inr": None,
                "pack_size": parsed["pack_size"],
                "image_url": None,
                "available_quantity": None,
                "is_ad": False,
                "query": query,
            }
        )
    return rows


def _harvest_products(
    envelope_or_text: dict[str, Any] | str, *, query: str | None = None
) -> list[dict[str, Any]]:
    """Walk a ``search_products`` response and produce normalised rows.

    Prefers ``structuredContent.products`` when present; falls back to
    parsing the numbered-list + ID-footer prose when the server only
    returned text. Accepts either an envelope dict or a raw text body
    (kept for the dump script's direct text-only path).
    """
    if isinstance(envelope_or_text, str):
        return _products_from_text(envelope_or_text, query=query)
    structured = _zepto_structured(envelope_or_text)
    if isinstance(structured, dict):
        products = structured.get("products")
        if isinstance(products, list):
            return [
                row
                for row in (
                    _product_from_structured(p, query=query)
                    for p in products
                    if isinstance(p, dict)
                )
                if row is not None
            ]
    return _products_from_text(_zepto_response_text(envelope_or_text), query=query)


def _harvest_multi_products(
    envelope_or_text: dict[str, Any] | str,
) -> list[dict[str, Any]]:
    """``search_multiple_products`` harvest. Prefers
    ``structuredContent.sections[{query, products[]}]``."""
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        if isinstance(structured, dict):
            sections = structured.get("sections")
            if isinstance(sections, list):
                rows: list[dict[str, Any]] = []
                for sec in sections:
                    if not isinstance(sec, dict):
                        continue
                    q = sec.get("query")
                    q_str = q.strip() if isinstance(q, str) else None
                    for p in sec.get("products") or []:
                        if not isinstance(p, dict):
                            continue
                        row = _product_from_structured(p, query=q_str)
                        if row is not None:
                            rows.append(row)
                return rows
        text = _zepto_response_text(envelope_or_text)
    else:
        text = envelope_or_text
    # Text fallback: section-splitting on the ``### Results for ...`` headers.
    if not text:
        return []
    headers = list(_RESULTS_SECTION_RE.finditer(text))
    if not headers:
        return _products_from_text(text)
    rows = []
    for i, hdr in enumerate(headers):
        q_str = hdr.group(1).strip()
        start = hdr.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        rows.extend(_products_from_text(text[start:end], query=q_str))
    return rows


def _address_from_structured(item: dict[str, Any]) -> dict[str, Any] | None:
    aid = item.get("id") or item.get("addressId") or item.get("address_id")
    label = item.get("label") or item.get("type") or item.get("name")
    if not isinstance(aid, str) or not aid.strip():
        return None
    if not isinstance(label, str) or not label.strip():
        return None
    return {
        "address_id": aid.strip(),
        "label": label.strip(),
        "address_line": (
            item.get("addressLine")
            or item.get("address_line")
            or item.get("formattedAddress")
        ),
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
    }


def _addresses_from_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    by_rank: dict[int, tuple[str, str]] = {}
    for m in _ADDR_FOOTER_RE.finditer(text):
        try:
            by_rank[int(m.group(1))] = (m.group(2), m.group(3))
        except ValueError:
            continue
    if not by_rank:
        return []
    rows: list[dict[str, Any]] = []
    line_re = re.compile(r"^\s*(\d+)\.\s+([^:]+?):\s*(.+)$")
    for line in text.splitlines():
        m = line_re.match(line)
        if not m:
            continue
        try:
            rank = int(m.group(1))
        except ValueError:
            continue
        pair = by_rank.get(rank)
        if not pair:
            continue
        label, aid = pair
        rows.append(
            {
                "address_id": aid,
                "label": label.strip(),
                "address_line": m.group(3).strip(),
                "latitude": None,
                "longitude": None,
            }
        )
    return rows


def _harvest_addresses(envelope_or_text: dict[str, Any] | str) -> list[dict[str, Any]]:
    """Lift saved-address rows out of a ``list_saved_addresses`` response."""
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        if isinstance(structured, dict):
            arr = structured.get("addresses")
            if isinstance(arr, list):
                return [
                    row
                    for row in (
                        _address_from_structured(a)
                        for a in arr
                        if isinstance(a, dict)
                    )
                    if row is not None
                ]
        return _addresses_from_text(_zepto_response_text(envelope_or_text))
    return _addresses_from_text(envelope_or_text)


def _harvest_store_id(envelope_or_text: dict[str, Any] | str) -> str | None:
    """Pick the primary store id out of a ``get_location_serviceability`` response."""
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        if isinstance(structured, dict):
            for k in ("primaryStoreId", "primary_store_id", "storeId", "store_id"):
                v = structured.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            for k in ("store", "primaryStore", "primary_store"):
                obj = structured.get(k)
                if isinstance(obj, dict):
                    for sk in ("id", "storeId", "store_id"):
                        v = obj.get(sk)
                        if isinstance(v, str) and v.strip():
                            return v.strip()
        text = _zepto_response_text(envelope_or_text)
    else:
        text = envelope_or_text
    if not text:
        return None
    m = _PRIMARY_STORE_RE.search(text)
    return m.group(1) if m else None


def _harvest_order_history_ids(envelope_or_text: dict[str, Any] | str) -> list[str]:
    """Pull order ids in display order out of ``list_order_history``."""
    orders: list[Any] = []
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        if isinstance(structured, dict):
            arr = structured.get("orders")
            if isinstance(arr, list):
                orders = arr
        if not orders:
            blob = _extract_embedded_json(_zepto_response_text(envelope_or_text))
            if isinstance(blob, dict) and isinstance(blob.get("orders"), list):
                orders = blob["orders"]
    else:
        blob = _extract_embedded_json(envelope_or_text)
        if isinstance(blob, dict) and isinstance(blob.get("orders"), list):
            orders = blob["orders"]
    out: list[str] = []
    for entry in orders:
        if not isinstance(entry, dict):
            continue
        oid = entry.get("id") or entry.get("orderId")
        if isinstance(oid, str) and oid.strip():
            out.append(oid.strip())
    return out


def _harvest_order_history_rows(envelope_or_text: dict[str, Any] | str) -> list[dict[str, Any]]:
    """Lift ``list_order_history`` into chat-renderable rows."""
    orders: list[Any] = []
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        if isinstance(structured, dict):
            arr = structured.get("orders")
            if isinstance(arr, list):
                orders = arr
        if not orders:
            blob = _extract_embedded_json(_zepto_response_text(envelope_or_text))
            if isinstance(blob, dict) and isinstance(blob.get("orders"), list):
                orders = blob["orders"]
    else:
        blob = _extract_embedded_json(envelope_or_text)
        if isinstance(blob, dict) and isinstance(blob.get("orders"), list):
            orders = blob["orders"]
    rows: list[dict[str, Any]] = []
    for entry in orders:
        if not isinstance(entry, dict):
            continue
        oid = entry.get("id") or entry.get("orderId")
        if not isinstance(oid, str) or not oid.strip():
            continue
        rows.append(
            {
                "order_id": oid.strip(),
                "code": entry.get("code"),
                "status": entry.get("formattedStatus") or entry.get("status"),
                "item_count": entry.get("itemQuantityCount"),
                "grand_total_paise": entry.get("grandTotalAmount"),
                "products": [
                    {"name": p.get("name"), "count": p.get("count")}
                    for p in entry.get("productsNamesAndCounts") or []
                    if isinstance(p, dict)
                ],
            }
        )
    return rows


def _harvest_order_id_from_create(envelope_or_text: dict[str, Any] | str) -> str | None:
    """``create_*_order`` responses include the freshly-issued order id."""
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        if isinstance(structured, dict):
            for k in ("orderId", "order_id", "id"):
                v = structured.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        text = _zepto_response_text(envelope_or_text)
    else:
        text = envelope_or_text
    blob = _extract_embedded_json(text)
    if isinstance(blob, dict):
        for k in ("orderId", "order_id", "id"):
            v = blob.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    m = re.search(rf"order[_ ]?id:?\s*({_UUID_RE})", text or "", re.IGNORECASE)
    return m.group(1) if m else None


def _harvest_cart_snapshot(
    envelope_or_text: dict[str, Any] | str,
    *,
    session_products: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Lift ``view_cart`` into a renderable cart snapshot.

    Prefers ``structuredContent`` (``{items, isEmpty, totalItems, ...}``);
    falls back to embedded-JSON / prose scanning when only text is
    returned.
    """
    structured: dict[str, Any] | None = None
    text: str = ""
    if isinstance(envelope_or_text, dict):
        structured = _zepto_structured(envelope_or_text)
        text = _zepto_response_text(envelope_or_text)
    else:
        text = envelope_or_text or ""

    blob: Any = structured
    if not isinstance(blob, dict):
        blob = _extract_embedded_json(text)

    if not isinstance(blob, dict):
        stripped = text.strip()
        if stripped and "empty" in stripped.lower():
            return {"is_empty": True, "items": [], "summary_text": stripped}
        return None

    raw_items = blob.get("items") or blob.get("cartItems") or []
    is_empty_flag = bool(blob.get("isEmpty") or blob.get("is_empty"))
    items: list[dict[str, Any]] = []

    by_pvid: dict[str, dict[str, Any]] = {}
    for p in session_products or []:
        pvid = p.get("product_variant_id")
        if isinstance(pvid, str):
            by_pvid[pvid] = p

    # Track per-item totals so we can synthesize ``grand_total_paise`` when
    # Zepto's view_cart structuredContent doesn't ship one (it doesn't —
    # the structured payload is ``{items, isEmpty, totalItems}`` only).
    computed_total_paise = 0
    has_priced_items = False

    if isinstance(raw_items, list):
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            pvid = (
                it.get("productVariantId")
                or it.get("product_variant_id")
                or it.get("variantId")
                or it.get("pvid")
            )
            spid = (
                it.get("storeProductId")
                or it.get("store_product_id")
                or it.get("spid")
            )
            qty_raw = it.get("quantity") or it.get("qty") or 1
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                qty = 1
            sess = by_pvid.get(pvid) if isinstance(pvid, str) else None
            price_paise = it.get("price")
            price_inr: float | None = None
            if isinstance(price_paise, (int, float)):
                price_inr = round(float(price_paise) / 100, 2)
                computed_total_paise += int(price_paise) * qty
                has_priced_items = True
            # Build a display-ready price_text from whichever signal we have.
            sess_price_text = (sess or {}).get("price_text")
            final_price_inr = price_inr if price_inr is not None else (sess or {}).get("price_inr")
            if isinstance(final_price_inr, (int, float)):
                price_text_out = sess_price_text or f"₹{final_price_inr:g}"
            else:
                price_text_out = sess_price_text
            items.append(
                {
                    "product_variant_id": pvid,
                    "store_product_id": spid,
                    "quantity": qty,
                    # Zepto's cart items carry their own ``name``; use it
                    # directly so a fresh session with no prior search
                    # still renders a labeled cart.
                    "name": it.get("name") or (sess or {}).get("name"),
                    "price_inr": final_price_inr,
                    "price_text": price_text_out,
                    "pack_size": (sess or {}).get("pack_size") or it.get("packSize"),
                    "image_url": (sess or {}).get("image_url") or it.get("imageUrl"),
                    # Sub-label (variant descriptor) when present — e.g.
                    # ``"The Baker's Dozen Zero Maida ..., Zero Preservatives"``.
                    "label": it.get("label") or None,
                    "available_quantity": it.get("availableQuantity"),
                }
            )

    # Prefer the server-reported total when present; otherwise fall back
    # to summing item prices × quantities.
    total_paise: int | None = None
    for k in ("grandTotalAmount", "totalAmount", "subtotal", "cartTotal"):
        v = blob.get(k)
        if isinstance(v, (int, float)):
            total_paise = int(v)
            break
    if total_paise is None and has_priced_items:
        total_paise = computed_total_paise

    is_empty = bool(is_empty_flag or not items)
    summary_text = text.strip() if (is_empty and text) else None
    return {
        "is_empty": is_empty,
        "items": items,
        "grand_total_paise": total_paise,
        "summary_text": summary_text,
    }


# ---------------------------------------------------------------------------
# LRU merge helper (mirrors ``_merge_recent_restaurants``)
# ---------------------------------------------------------------------------
def _merge_recent(
    existing: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    *,
    key: str,
    cap: int,
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for entry in existing:
        if not isinstance(entry, dict):
            continue
        k = entry.get(key)
        if isinstance(k, str) and k.strip():
            by_key[k.strip()] = entry
    # New rows overwrite (latest snapshot wins).
    for entry in new_rows:
        if not isinstance(entry, dict):
            continue
        k = entry.get(key)
        if not isinstance(k, str) or not k.strip():
            continue
        by_key[k.strip()] = entry
    # Preserve insertion order (Python dicts are ordered), keep last ``cap``.
    rows = list(by_key.values())
    if len(rows) > cap:
        rows = rows[-cap:]
    return rows


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------
def _stable_device_id(user_id: str | None) -> str:
    """Generate a stable device id from the Companion user id.

    Zepto's ``update_cart`` requires a ``deviceId`` string. The planner
    just emits the ``__ZEPTO_DEVICE_ID__`` placeholder; we mint a
    deterministic UUID-shaped string here so re-runs across processes
    keep the same id.
    """
    seed = (user_id or "anon").encode()
    h = hashlib.sha1(b"zepto-device|" + seed).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _resolve_coords(
    *,
    placeholder_values: dict[str, str],
    location_text: str | None,
    user_geo: dict[str, Any] | None,
) -> tuple[float, float] | None:
    """Choose lat/lng for ``__USER_LAT__`` / ``__USER_LNG__``.

    Preference order: explicit ``user_geo`` from the request, then
    geocoded ``location_text``, then anything already in
    ``placeholder_values`` (carried from a previous turn).
    """
    if isinstance(user_geo, dict):
        try:
            lat = float(user_geo.get("latitude"))  # type: ignore[arg-type]
            lng = float(user_geo.get("longitude"))  # type: ignore[arg-type]
            return lat, lng
        except (TypeError, ValueError):
            pass

    # Carried-forward placeholder coords from prior turn take precedence
    # over geocoding (geocoding's noisier and re-runs would slowly drift).
    try:
        lat = float(placeholder_values.get("__USER_LAT__", ""))
        lng = float(placeholder_values.get("__USER_LNG__", ""))
        return lat, lng
    except (TypeError, ValueError):
        pass

    text = (location_text or "").strip()
    if text:
        try:
            from companion_api.google_maps import GeoPoint, geocode_address

            point = geocode_address(text)
            if isinstance(point, GeoPoint):
                return point.latitude, point.longitude
        except Exception:  # noqa: BLE001
            logger.warning(
                "Zepto geocode failed for %r", text, exc_info=True
            )

    return None


def _unresolved_zepto_placeholders(
    obj: Any, values: dict[str, str]
) -> list[str]:
    found: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, str) and o in _ZEPTO_PLACEHOLDER_ALIASES:
            if o not in values or not str(values.get(o, "")).strip():
                found.add(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return sorted(found)


# ---------------------------------------------------------------------------
# Session-state harvesting (placeholders + LRU indexes)
# ---------------------------------------------------------------------------
def _learn_zepto_placeholders(
    values: dict[str, str],
    tool: str,
    envelope: dict[str, Any],
    *,
    new_products: list[dict[str, Any]] | None = None,
    new_addresses: list[dict[str, Any]] | None = None,
    user_geo: dict[str, Any] | None = None,
) -> None:
    if tool in {"search_products", "search_multiple_products"} and new_products:
        first = new_products[0]
        pvid = first.get("product_variant_id")
        spid = first.get("store_product_id")
        if isinstance(pvid, str) and pvid.strip():
            values["__ZEPTO_PVID__"] = pvid.strip()
        if isinstance(spid, str) and spid.strip():
            values["__ZEPTO_SPID__"] = spid.strip()

    if tool == "list_saved_addresses" and new_addresses:
        # Prefer the saved address nearest to the device location (issue #18),
        # else fall back to the first row.
        chosen_aid: str | None = None
        if user_geo:
            try:
                from app.services.address_nearest import nearest_address_id

                chosen_aid = nearest_address_id(new_addresses, user_geo)
            except Exception:
                logger.debug("zepto nearest-address pick failed", exc_info=True)
        if not chosen_aid:
            aid = new_addresses[0].get("address_id")
            chosen_aid = aid.strip() if isinstance(aid, str) and aid.strip() else None
        if chosen_aid:
            values.setdefault("__ZEPTO_ADDRESS_ID__", chosen_aid)

    if tool == "get_location_serviceability":
        sid = _harvest_store_id(envelope)
        if sid:
            values["__ZEPTO_STORE_ID__"] = sid

    if tool == "list_order_history":
        ids = _harvest_order_history_ids(envelope)
        if ids:
            values["__ZEPTO_ORDER_ID__"] = ids[0]

    if tool in {
        "create_order",
        "create_online_payment_order",
        "create_wallet_order",
        "create_upi_reserve_pay_order",
    }:
        oid = _harvest_order_id_from_create(envelope)
        if oid:
            values["__ZEPTO_ORDER_ID__"] = oid


# ---------------------------------------------------------------------------
# Cart-item shape repair (defensive, narrow)
# ---------------------------------------------------------------------------
# Zepto's update_cart needs ``productVariantId`` + ``storeProductId`` per
# item. Occasionally the planner emits snake_case keys; we repair them so
# we don't bounce a request the planner clearly intended.
_CART_KEY_FIX = {
    "product_variant_id": "productVariantId",
    "pvid": "productVariantId",
    "store_product_id": "storeProductId",
    "spid": "storeProductId",
    "qty": "quantity",
}


def _repair_cart_items(args: dict[str, Any]) -> dict[str, Any]:
    items = args.get("cartItems")
    if not isinstance(items, list):
        return args
    fixed: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        repaired: dict[str, Any] = {}
        for k, v in it.items():
            repaired[_CART_KEY_FIX.get(k, k)] = v
        # ``quantity`` defaults to 1 if missing (Zepto schema doesn't
        # default it itself).
        repaired.setdefault("quantity", 1)
        fixed.append(repaired)
    out = dict(args)
    out["cartItems"] = fixed
    return out


# ---------------------------------------------------------------------------
# Session execution loop
# ---------------------------------------------------------------------------
async def _run_zepto_session_inner(
    tool_plan: list[dict[str, Any]],
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None,
    *,
    location_text: str | None = None,
    user_geo: dict[str, Any] | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_zepto_products: list[dict[str, Any]] | None = None,
    session_zepto_addresses: list[dict[str, Any]] | None = None,
    session_zepto_cart: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:
        raise RuntimeError(
            "Python MCP SDK is not installed. Use Python 3.10+ and install requirements.txt."
        ) from e

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_location_text": location_text,
        "executor": "zepto.mcp_executor",
    }

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError(
            "mcp-remote is not installed — run npm install at the repo root."
        )

    ensure_mcp_tokens_on_disk(repo_root, mcp_url=mcp_url, user_id=user_id)

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    # ---- Seed placeholders from the carried-forward session ----
    placeholder_values: dict[str, str] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k.startswith("__") and k.endswith("__"):
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()

    # Device id is always available (deterministic from user_id).
    placeholder_values.setdefault("__ZEPTO_DEVICE_ID__", _stable_device_id(user_id))

    # Seed lat/lng (same UX role as Zomato's __ADDRESS_ID__: the user
    # shouldn't have to think about coords).
    coords = _resolve_coords(
        placeholder_values=placeholder_values,
        location_text=location_text,
        user_geo=user_geo,
    )
    if coords:
        placeholder_values["__USER_LAT__"] = str(coords[0])
        placeholder_values["__USER_LNG__"] = str(coords[1])
        diagnostics["resolved_location"] = {
            "latitude": coords[0],
            "longitude": coords[1],
        }

    # Hydrate session-index seeds from carried-forward state.
    recent_products = list(session_zepto_products or [])
    recent_addresses = list(session_zepto_addresses or [])
    cart_snapshot: dict[str, Any] | None = (
        dict(session_zepto_cart) if isinstance(session_zepto_cart, dict) else None
    )

    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())

    # ---- Execute the plan ----------------------------------------------
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(
                session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS
            )
            step_index = 0

            for step in tool_plan:
                tool = str(step.get("tool") or "").strip()
                if not tool:
                    continue
                raw_args = dict(step.get("arguments") or {})

                # Clear stale placeholders this tool is about to issue.
                for ph in _ZEPTO_TOOL_ISSUES_PLACEHOLDER.get(tool, ()):
                    placeholder_values.pop(ph, None)
                    # If a tool issues an id and the planner *also* emitted
                    # a placeholder for the same id (chained inside one
                    # turn), the second step would skip. So re-issuing
                    # placeholders is OK — they'll be re-learned post-call.

                # Substitute placeholders and coerce lat/lng strings → floats.
                exec_args = substitute_placeholders(
                    copy.deepcopy(raw_args), placeholder_values
                )
                exec_args = _coerce_latlng_floats(
                    exec_args if isinstance(exec_args, dict) else {}
                )

                # Narrow defensive shape repair for ``update_cart``.
                if tool == "update_cart" and isinstance(exec_args, dict):
                    exec_args = _repair_cart_items(exec_args)

                unresolved = _unresolved_zepto_placeholders(
                    exec_args, placeholder_values
                )
                if unresolved:
                    diagnostics.setdefault("skipped_tools", []).append(
                        {
                            "tool": tool,
                            "reason": "unresolved_placeholders",
                            "placeholders": sorted(unresolved),
                        }
                    )
                    step_index += 1
                    continue

                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=exec_args or {}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(result)
                step_results.append(
                    {
                        "tool": tool,
                        "arguments": exec_args or {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1

                if _zepto_is_error(envelope):
                    continue

                new_products: list[dict[str, Any]] = []
                new_addresses: list[dict[str, Any]] = []

                if tool == "search_products":
                    new_products = _harvest_products(
                        envelope,
                        query=str(exec_args.get("query") or "").strip() or None,
                    )
                elif tool == "search_multiple_products":
                    new_products = _harvest_multi_products(envelope)
                elif tool == "list_saved_addresses":
                    new_addresses = _harvest_addresses(envelope)
                elif tool == "view_cart":
                    snap = _harvest_cart_snapshot(
                        envelope, session_products=recent_products
                    )
                    if snap is not None:
                        cart_snapshot = snap
                elif tool == "update_cart":
                    cart_snapshot = _project_cart_from_update_args(
                        exec_args, recent_products, prior=cart_snapshot
                    )
                elif tool == "list_order_history":
                    diagnostics["zepto_orders"] = _harvest_order_history_rows(
                        envelope
                    )

                _learn_zepto_placeholders(
                    placeholder_values,
                    tool,
                    envelope,
                    new_products=new_products or None,
                    new_addresses=new_addresses or None,
                    user_geo=user_geo,
                )

                if new_products:
                    recent_products = _merge_recent(
                        recent_products,
                        new_products,
                        key="product_variant_id",
                        cap=_MAX_REMEMBERED,
                    )
                if new_addresses:
                    recent_addresses = _merge_recent(
                        recent_addresses,
                        new_addresses,
                        key="address_id",
                        cap=max(_MAX_REMEMBERED, 16),
                    )

    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    diagnostics["session_placeholders"] = {
        k: v for k, v in placeholder_values.items() if k in _ZEPTO_SESSION_PERSISTENT
    }
    if recent_products:
        diagnostics["session_zepto_products"] = list(recent_products)
    if recent_addresses:
        diagnostics["session_zepto_addresses"] = list(recent_addresses)
    if cart_snapshot is not None:
        diagnostics["session_zepto_cart"] = cart_snapshot
    return step_results, diagnostics


def _project_cart_from_update_args(
    args: dict[str, Any],
    session_products: list[dict[str, Any]],
    *,
    prior: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Optimistically build a cart snapshot from the args we just sent.

    Zepto's ``update_cart`` returns a short confirmation, not the new
    cart contents — but the next ``view_cart`` would. We don't want to
    chain a second call automatically (executor stays planner-first), so
    we project what the cart *should* look like locally so the UI can
    update without a round-trip. The next ``view_cart`` the planner runs
    will overwrite this snapshot with the authoritative one.
    """
    items_arg = args.get("cartItems")
    if not isinstance(items_arg, list):
        return prior
    by_pvid: dict[str, dict[str, Any]] = {}
    for p in session_products:
        pvid = p.get("product_variant_id")
        if isinstance(pvid, str):
            by_pvid[pvid] = p

    prior_items = []
    if isinstance(prior, dict) and isinstance(prior.get("items"), list):
        prior_items = list(prior["items"])
    by_pvid_existing: dict[str, dict[str, Any]] = {}
    for it in prior_items:
        pvid = it.get("product_variant_id")
        if isinstance(pvid, str):
            by_pvid_existing[pvid] = it

    replace_cart = bool(args.get("replaceCart"))
    if replace_cart:
        by_pvid_existing.clear()

    for it in items_arg:
        if not isinstance(it, dict):
            continue
        pvid = it.get("productVariantId") or it.get("product_variant_id")
        spid = it.get("storeProductId") or it.get("store_product_id")
        qty = it.get("quantity")
        if not isinstance(pvid, str) or not pvid.strip():
            continue
        if qty == 0:
            by_pvid_existing.pop(pvid, None)
            continue
        sess = by_pvid.get(pvid, {})
        by_pvid_existing[pvid] = {
            "product_variant_id": pvid,
            "store_product_id": spid,
            "quantity": qty,
            "name": sess.get("name"),
            "price_inr": sess.get("price_inr"),
            "price_text": sess.get("price_text"),
            "pack_size": sess.get("pack_size"),
        }

    items = list(by_pvid_existing.values())
    return {
        "is_empty": not items,
        "items": items,
        "grand_total_paise": (
            sum(
                int((it.get("price_inr") or 0) * 100) * int(it.get("quantity") or 0)
                for it in items
            )
            if items
            else None
        ),
        "summary_text": None if items else "Cart is empty.",
    }


def run_zepto_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    location_text: str | None = None,
    user_geo: dict[str, Any] | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_zepto_products: list[dict[str, Any]] | None = None,
    session_zepto_addresses: list[dict[str, Any]] | None = None,
    session_zepto_cart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("ZEPTO_MCP_URL") or _DEFAULT_ZEPTO_MCP_URL).strip()
    node = node_binary or os.getenv("NODE_BINARY", "node")

    cleaned: list[dict[str, Any]] = []
    for s in tool_plan:
        if isinstance(s, dict):
            tn = str(s.get("tool") or "").strip()
            if tn:
                cleaned.append(
                    {"tool": tn, "arguments": dict(s.get("arguments") or {})}
                )

    try:
        steps, diagnostics = asyncio.run(
            _run_zepto_session_inner(
                cleaned,
                root,
                url,
                node,
                user_id,
                location_text=location_text,
                user_geo=user_geo,
                session_placeholders=session_placeholders,
                session_zepto_products=session_zepto_products,
                session_zepto_addresses=session_zepto_addresses,
                session_zepto_cart=session_zepto_cart,
            )
        )
        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
    except BaseException as e:
        logger.exception("Zepto MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e, url),
            "steps": [],
            "diagnostics": {},
        }


def list_zepto_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    from app.services.zomato.mcp_executor import list_mcp_tools_sync

    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("ZEPTO_MCP_URL") or _DEFAULT_ZEPTO_MCP_URL).strip()
    return list_mcp_tools_sync(
        repo_root=root,
        mcp_url=url,
        node_binary=node_binary,
        user_id=user_id,
    )
