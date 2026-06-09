"""Swiggy Food cart-card builder (clean port of `_extract_swiggy_cart_snapshot`).

Swiggy carts are single-restaurant replacement state (like Zomato's). The snapshot
is a pure function of a `get_food_cart` / `update_food_cart` envelope, shaped to the
same `SessionCart` contract the frontend already renders for Zomato. Swiggy doesn't
echo the restaurant name in the cart envelope, so we take it from the cart tool's
own args (restaurantName/restaurantId) instead of threaded session state.
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_CART_TOOLS = {"get_food_cart", "update_food_cart"}


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _cart_root(data: Any) -> dict[str, Any] | None:
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        has_id = (
            node.get("cart_id") or node.get("cartId") or node.get("orderId") or node.get("order_id")
        )
        has_items = node.get("cartItems") or node.get("cart_items") or node.get("items")
        if has_id and has_items:
            return node
    return None


def _cart_item(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    iid = (
        row.get("menu_item_id") or row.get("menuItemId") or row.get("itemId") or row.get("item_id")
    )
    name = row.get("name") or row.get("item_name") or row.get("itemName")
    if not iid or not isinstance(name, str) or not name.strip():
        return None
    original = row.get("subtotal") or row.get("total") or row.get("price")
    discounted = row.get("final_price")
    tags = ["veg"] if str(row.get("is_veg")) in {"1", "true", "True"} else []
    variant_parts = []
    for v in row.get("variants") or []:
        if isinstance(v, dict) and (v.get("name") or "").strip():
            gn = (v.get("group_name") or "").strip()
            variant_parts.append(f"{gn}: {v['name'].strip()}" if gn else v["name"].strip())
    addons = []
    for a in row.get("addons") or []:
        an = (a.get("name") or a.get("addon_name") or "").strip() if isinstance(a, dict) else ""
        if an:
            addons.append(
                {
                    "id": str(a.get("choice_id") or a.get("id") or "").strip() or None,
                    "group_id": str(a.get("group_id") or "").strip() or None,
                    "name": an,
                    "add_on_group_name": a.get("group_name") or None,
                    "quantity": a.get("quantity") or 1,
                }
            )
    iid_s = str(iid).strip()
    out: dict[str, Any] = {
        "menu_item_id": iid_s,
        "variant_id": iid_s,  # alias for the frontend's id extractor
        "item_name": name.strip(),
        "quantity": row.get("quantity") or 1,
        "item_tags": tags,
    }
    if isinstance(original, int | float) and original > 0:
        out["price"] = original
    if isinstance(discounted, int | float) and discounted > 0:
        out["discounted_price"] = discounted
    if variant_parts:
        out["description"] = " · ".join(variant_parts)
    img = row.get("imageUrl") or row.get("image_url")
    if isinstance(img, str) and img.strip():
        out["image_url"] = img.strip()
    if addons:
        out["add_ons"] = addons
    return out


def _charge_breakdown(pricing: dict[str, Any]) -> dict[str, Any] | None:
    delivery = pricing.get("delivery_charge") or pricing.get("deliveryCharge")
    taxes = pricing.get("taxes_and_charges") or pricing.get("taxes")
    base = (
        [{"amount": float(delivery), "charge_type": "CHARGE_ID_DELIVERY_CHARGE"}]
        if isinstance(delivery, int | float) and delivery > 0
        else []
    )
    tax = (
        [{"tax_amount": float(taxes), "tax_type": "GST"}]
        if isinstance(taxes, int | float) and taxes > 0
        else []
    )
    return {"base_charges": base, "taxes": tax} if (base or tax) else None


def _eta(cart_root: dict[str, Any]) -> str | None:
    rest = cart_root.get("restaurant") if isinstance(cart_root.get("restaurant"), dict) else {}
    subtitle = rest.get("deliverySubtitle") or rest.get("delivery_subtitle")
    if not isinstance(subtitle, str) or not subtitle.strip():
        return None
    return subtitle.split(" to ", 1)[0].strip() if " to " in subtitle else subtitle.strip()


def cart_snapshot(
    envelope: dict[str, Any], *, args: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return None
    cart_root = _cart_root(envelope.get("data"))
    if not cart_root:
        return None
    items = [
        c
        for c in (
            _cart_item(r)
            for r in (
                cart_root.get("cartItems")
                or cart_root.get("cart_items")
                or cart_root.get("items")
                or []
            )
        )
        if c
    ]
    if not items:
        return None
    args = args or {}
    cart_id = (
        cart_root.get("cart_id")
        or cart_root.get("cartId")
        or cart_root.get("orderId")
        or cart_root.get("order_id")
    )
    res_id = (
        cart_root.get("restaurantId")
        or cart_root.get("restaurant_id")
        or cart_root.get("res_id")
        or args.get("restaurantId")
    )
    rname = (
        cart_root.get("restaurantName")
        or cart_root.get("restaurant_name")
        or args.get("restaurantName")
    )
    pricing = cart_root.get("pricing") if isinstance(cart_root.get("pricing"), dict) else {}
    offers = cart_root.get("offers") if isinstance(cart_root.get("offers"), dict) else {}
    to_pay = pricing.get("to_pay") or pricing.get("toPay") or pricing.get("total")
    methods = (
        (envelope.get("data") or {}).get("availablePaymentMethods")
        if isinstance(envelope.get("data"), dict)
        else None
    )
    payment = (
        methods[0].strip()
        if isinstance(methods, list)
        and methods
        and isinstance(methods[0], str)
        and methods[0].strip()
        else None
    )
    snap: dict[str, Any] = {
        "cart_id": str(cart_id).strip() if cart_id else None,
        "res_id": str(res_id).strip() if res_id is not None else None,
        "restaurant_name": str(rname).strip() if isinstance(rname, str) and rname.strip() else None,
        "items": items,
        "item_total": pricing.get("item_total")
        if isinstance(pricing.get("item_total"), int | float)
        else None,
        "final_amount": to_pay if isinstance(to_pay, int | float) else None,
        "payment_method_type": payment,
        "eta": _eta(cart_root),
    }
    cb = _charge_breakdown(pricing)
    if cb:
        snap["charge_breakdown"] = cb
    coupon = offers.get("coupon_applied")
    if isinstance(coupon, str) and coupon.strip():
        snap["promo_code"] = coupon.strip()
    disc = offers.get("coupon_discount")
    if isinstance(disc, int | float) and disc > 0:
        snap["promo_discount_amount"] = float(disc)
    return snap


def build_cart_cards(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if tr.tool not in _CART_TOOLS or not tr.ok:
            continue
        snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {}, args=tr.arguments)
        if snap and snap.get("items"):
            out.append(
                Attachment(
                    kind="cart_card", source=f"swiggy_food.{tr.tool}", provider="swiggy", cart=snap
                )
            )
    return out
