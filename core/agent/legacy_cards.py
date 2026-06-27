"""Map the normalized model → the EXISTING frontend's attachment shapes, so the
already-built (polished) Instamart/Zepto product + cart cards render unchanged.

The old chat renders `attachments:[{kind:'instamart_product_cards', instamart_products:[…]}, …]`
(see frontend `ChatLog.AttachmentList`). We emit exactly those shapes. Prices: the FE wants
`*_inr` (rupees); our internal money is minor units (paise).
"""

from __future__ import annotations

from typing import Any

from core.agent.contracts import Cart, Product, fmt_minor


def _inr(minor: int | None) -> float | None:
    return None if minor is None else round((minor or 0) / 100, 2)


def _mrp_inr(p: Product) -> float | None:
    return _inr(p.extra.get("mrp_minor") or p.price_minor)


def _im_variant(p: Product) -> dict[str, Any]:
    return {
        "spin_id": p.item_id,
        "quantity_description": p.variation or None,
        "price_inr": _inr(p.price_minor),
        "mrp_inr": _mrp_inr(p),
        "image_url": p.image_url or None,
        "in_stock": p.in_stock,
    }


def products_attachment(pid: str, query: str, products: list[Product]) -> dict[str, Any] | None:
    if pid == "instamart":
        # Group the flat variants back into ONE card per product (by parent_product_id), so the
        # FE's size-picker shows all pack sizes under a single product instead of N cards.
        order: list[str] = []
        groups: dict[str, dict[str, Any]] = {}
        for p in products:
            parent = p.extra.get("parent_product_id") or p.item_id
            if parent not in groups:
                order.append(parent)
                groups[parent] = {
                    "product_id": parent,
                    "parent_product_id": parent,
                    "name": p.name,
                    "brand": p.brand or None,
                    "is_promoted": False,
                    "query": query,
                    "variants": [],
                }
            groups[parent]["variants"].append(_im_variant(p))
        return {"kind": "instamart_product_cards", "products": [groups[k] for k in order]}
    if pid == "zepto":
        return {
            "kind": "zepto_product_cards",
            # FE ZeptoProductCardGrid also reads `attachment.products`.
            "products": [
                {
                    "product_variant_id": p.item_id,
                    "store_product_id": p.secondary_id or "",
                    "name": p.name,
                    "price_inr": _inr(p.price_minor),
                    "price_text": fmt_minor(p.price_minor),
                    "mrp_inr": _mrp_inr(p),
                    "pack_size": p.variation or None,
                    "image_url": p.image_url or None,
                    # Carry the query so the FE groups a multi-item search into per-item sections,
                    # exactly like Instamart (without it, sooji+besan collapse into one scroller).
                    "query": query,
                }
                for p in products
            ],
        }
    return None


def cart_attachment(pid: str, cart: Cart, address_id: str | None = None) -> dict[str, Any] | None:
    if pid == "instamart":
        return {
            "kind": "instamart_cart_card",
            "delivery_address_id": address_id,
            "instamart_cart": {
                "is_empty": cart.is_empty,
                "items": [
                    {
                        "spin_id": i.item_id,
                        "quantity": i.qty,
                        "name": i.name,
                        "price_inr": _inr(i.price_minor),
                        "image_url": i.image_url or None,
                    }
                    for i in cart.items
                ],
                "grand_total_paise": cart.total_minor,
                "bill_breakdown": [
                    {
                        "label": b.get("label"),
                        "value": fmt_minor(b.get("minor", 0)),
                        "amount_paise": b.get("minor"),
                    }
                    for b in (cart.bill or [])
                ],
                "selected_address_id": address_id,
            },
        }
    if pid == "zepto":
        return {
            "kind": "zepto_cart_card",
            "delivery_address_id": address_id,
            "zepto_cart": {
                "is_empty": cart.is_empty,
                "items": [
                    {
                        "product_variant_id": i.item_id,
                        "store_product_id": i.secondary_id or None,
                        "quantity": i.qty,
                        "name": i.name,
                        "price_inr": _inr(i.price_minor),
                        "image_url": i.image_url or None,
                    }
                    for i in cart.items
                ],
                "grand_total_paise": cart.total_minor,
                # Item Total + Delivery & charges from get_payment_methods (empty until priced).
                "bill_breakdown": [
                    {
                        "label": b.get("label"),
                        "value": fmt_minor(b.get("minor", 0)),
                        "amount_paise": b.get("minor"),
                    }
                    for b in (cart.bill or [])
                ],
                # Store closed / not serviceable → the card prompts an address change.
                "unavailable": cart.unavailable or None,
            },
        }
    return None
