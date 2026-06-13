"""Swiggy Instamart product-card builders.

Products are grouped at the parent-product level (one row per product, variants
as a sub-list) so the UI renders one card per product and the LRU stays compact.
Only the top 5 products per search are kept — variants beyond the parent cap are
preserved inside their product row.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_PRODUCT_TOOLS = {"search_products", "your_go_to_items"}
_MAX_PRODUCTS_PER_SEARCH = 5


def _coerce_price_inr(price_obj: Any) -> tuple[float | None, float | None]:
    """Extract (offer_price_inr, mrp_inr) from a variation's price dict."""
    if not isinstance(price_obj, dict):
        return (None, None)
    mrp = price_obj.get("mrp")
    offer = price_obj.get("offerPrice") or price_obj.get("offer_price") or mrp
    try:
        mrp_inr = float(mrp) if mrp is not None else None
    except (TypeError, ValueError):
        mrp_inr = None
    try:
        offer_inr = float(offer) if offer is not None else mrp_inr
    except (TypeError, ValueError):
        offer_inr = mrp_inr
    return (offer_inr, mrp_inr)


def products_from_search(envelope: dict[str, Any], *, query: str | None) -> list[dict[str, Any]]:
    """Return up to 5 product-level rows, each with a `variants` list (all pack sizes)."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    products = data.get("products")
    if not isinstance(products, list):
        return []
    out: list[dict[str, Any]] = []
    for prod in products[:_MAX_PRODUCTS_PER_SEARCH]:
        if not isinstance(prod, dict):
            continue
        parent_name = str(prod.get("displayName") or "").strip()
        brand = str(prod.get("brand") or "").strip()
        product_id = str(prod.get("productId") or "").strip()
        parent_product_id = str(prod.get("parentProductId") or "").strip()
        is_promoted = bool(prod.get("isPromoted"))
        raw_vars = prod.get("variations") if isinstance(prod.get("variations"), list) else []
        variants: list[dict[str, Any]] = []
        for var in raw_vars:
            if not isinstance(var, dict):
                continue
            spin_id = str(var.get("spinId") or "").strip()
            if not spin_id:
                continue
            offer_inr, mrp_inr = _coerce_price_inr(var.get("price"))
            variants.append(
                {
                    "spin_id": spin_id,
                    "quantity_description": str(var.get("quantityDescription") or "").strip()
                    or None,
                    "price_inr": offer_inr,
                    "mrp_inr": mrp_inr,
                    "image_url": str(var.get("imageUrl") or "").strip() or None,
                    "in_stock": bool(var.get("isInStockAndAvailable")),
                }
            )
        if not variants:
            continue
        out.append(
            {
                "product_id": product_id or None,
                "parent_product_id": parent_product_id or None,
                "name": parent_name,
                "brand": brand or None,
                "is_promoted": is_promoted,
                "query": query or None,
                "variants": variants,
            }
        )
    return out


def _product_card(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or "").strip()
    variants = row.get("variants")
    if not name or not isinstance(variants, list) or not variants:
        return None
    return {
        "product_id": row.get("product_id"),
        "parent_product_id": row.get("parent_product_id"),
        "name": name,
        "brand": row.get("brand"),
        "is_promoted": row.get("is_promoted", False),
        "query": row.get("query"),
        "variants": variants,
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
                    kind="instamart_product_cards",
                    provider="swiggy_instamart",
                    source=f"swiggy_instamart.{tr.tool}",
                    products=cards,
                )
            )
    return out
