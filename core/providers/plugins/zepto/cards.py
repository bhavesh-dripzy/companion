"""Zepto product-card builders (clean port of the legacy structured harvest).

Ported from `zepto.mcp_executor._product_from_structured` / `_zepto_structured` +
`agents.pipeline._build_zepto_product_card`, adapted to operate on `ToolResult`s.
The live Zepto server returns structured `data.products[]` (prices in paise); we
normalize to integer rupees so the UI formats consistently with the other
providers. The legacy prose-fallback parser is intentionally not ported — the
hosted server returns structured content; on the rare text-only reply this yields
no cards (the adapter path still covers it until cutover). Validated against a
real captured fixture.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_PRODUCT_TOOLS = {"search_products", "search_multiple_products", "get_past_order_items"}


def _structured(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """The structured payload, or None for `{raw_text: ...}` text-only replies."""
    data = envelope.get("data")
    if not isinstance(data, dict) or set(data.keys()) == {"raw_text"}:
        return None
    return data


def _row_from_structured(item: Any, *, query: str | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    pvid = (
        item.get("productVariantId")
        or item.get("product_variant_id")
        or item.get("variantId")
        or item.get("id")
    )
    spid = item.get("storeProductId") or item.get("store_product_id")
    name = item.get("name")
    if not (isinstance(pvid, str) and pvid.strip()):
        return None
    if not (isinstance(spid, str) and spid.strip()):
        return None
    if not (isinstance(name, str) and name.strip()):
        return None
    # Zepto prices are in paise (₹49 → 4900); normalize to rupees.
    price_paise = item.get("price")
    mrp_paise = item.get("mrp")
    price_inr = round(float(price_paise) / 100, 2) if isinstance(price_paise, int | float) else None
    mrp_inr = round(float(mrp_paise) / 100, 2) if isinstance(mrp_paise, int | float) else None
    return {
        "product_variant_id": pvid.strip(),
        "store_product_id": spid.strip(),
        "name": name.strip(),
        "price_inr": price_inr,
        "price_text": f"₹{price_inr:g}" if price_inr is not None else None,
        "mrp_inr": mrp_inr,
        "pack_size": item.get("packSize") or item.get("pack_size") or item.get("unit"),
        "image_url": item.get("imageUrl") or item.get("image_url"),
        "query": query,
    }


def products_from_search(envelope: dict[str, Any], *, query: str | None) -> list[dict[str, Any]]:
    """Normalized product rows from a structured search/multi-search envelope."""
    structured = _structured(envelope)
    if not isinstance(structured, dict):
        return []
    rows: list[dict[str, Any]] = []
    products = structured.get("products")
    if isinstance(products, list):
        rows.extend(r for r in (_row_from_structured(p, query=query) for p in products) if r)
    for sec in structured.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        q = sec.get("query")
        q_str = q.strip() if isinstance(q, str) and q.strip() else query
        for p in sec.get("products") or []:
            row = _row_from_structured(p, query=q_str)
            if row:
                rows.append(row)
    return rows


def _product_card(row: dict[str, Any]) -> dict[str, Any] | None:
    pvid = str(row.get("product_variant_id") or "").strip()
    spid = str(row.get("store_product_id") or "").strip()
    name = str(row.get("name") or "").strip()
    if not pvid or not spid or not name:
        return None
    return {
        "product_variant_id": pvid,
        "store_product_id": spid,
        "name": name,
        "price_inr": row.get("price_inr"),
        "price_text": row.get("price_text"),
        "pack_size": row.get("pack_size"),
        "image_url": row.get("image_url"),
        "query": row.get("query"),
    }


def build_product_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _PRODUCT_TOOLS or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        query = str((tr.arguments or {}).get("query") or "").strip() or None
        cards = [
            c for c in (_product_card(r) for r in products_from_search(envelope, query=query)) if c
        ]
        if cards:
            out.append(
                Attachment(
                    kind="zepto_product_cards",
                    provider="zepto",
                    source=f"zepto.{tr.tool}",
                    products=cards,
                )
            )
    return out
