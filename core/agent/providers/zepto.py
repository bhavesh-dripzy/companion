"""Zepto adapter (grocery). UPSERT-or-replace cart, read serviceability; prices in PAISE.

Tools: search_products / search_multiple_products / view_cart / update_cart (deviceId+cartItems
+replaceCart) / get_location_serviceability / select_saved_address. Item id = productVariantId
(+ storeProductId). The cart response carries name + price; only imageUrl is null. See zepto_poc.md.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from core.agent.contracts import Cart, CartItem, CartResult, ErrorKind, Product
from core.agent.providers.base import (
    BATCH_SEARCHABLE,
    CARTABLE,
    CHECKOUTABLE,
    SEARCHABLE,
    SERVICEABLE,
    ExecCtx,
    ProviderAdapter,
)

# Zepto's ImageKit "ik-seo" image URLs embed the product NAME in the path; names with "/", "|" or
# spaces (e.g. "Brinjal Small / Nagpur") produce a malformed URL → 404. We percent-encode just the
# name segment so the CDN serves it (verified live: raw → unreachable, encoded → 200).
_IK_NAME = re.compile(r"(/product_variant/[0-9a-fA-F-]{36}/)(.+)$")


def _safe_image_url(url: str) -> str:
    m = _IK_NAME.search(url or "")
    return url if not m else url[: m.start(2)] + quote(m.group(2), safe="")


class ZeptoProvider(ProviderAdapter):
    id = "zepto"
    domains = ("grocery",)
    mcp_url = "https://mcp.zepto.co.in/mcp"
    capabilities = frozenset({SEARCHABLE, BATCH_SEARCHABLE, CARTABLE, SERVICEABLE, CHECKOUTABLE})
    cart_strategy = "upsert_robust"
    free_delivery_minor = 9900  # ₹99 minimum order for free delivery

    async def ensure_context(self, session, ctx: ExecCtx) -> None:
        # Zepto is store-scoped: selecting the saved address pins the session store.
        if ctx.address_id:
            await self._call(session, "select_saved_address", {"addressId": ctx.address_id})

    async def serviceable(self, session, ctx: ExecCtx) -> tuple[bool, str]:
        if ctx.lat is None or ctx.lng is None:
            return True, ""
        raw = await self._call(
            session, "get_location_serviceability", {"latitude": ctx.lat, "longitude": ctx.lng}
        )
        text = raw.get("text", "")
        ok = ("delivers to your location" in text.lower()) or ("✅" in text)
        return ok, text

    async def default_context(self, session, user_id: str) -> dict[str, Any]:
        addrs = await self.list_addresses(session)
        if not addrs:
            return {}
        a = addrs[0]
        return {
            "address_id": a["id"],
            "lat": a.get("lat"),
            "lng": a.get("lng"),
            "device_id": f"companion-{user_id}",
        }

    async def list_addresses(self, session) -> list[dict[str, Any]]:
        raw = await self._call(session, "list_saved_addresses", {})
        addrs = (raw.get("structured") or {}).get("addresses") or []
        return [
            {
                "id": a.get("id") or a.get("addressId"),
                "label": " · ".join(x for x in (a.get("label"), a.get("addressLine")) if x)
                or a.get("label", ""),
                "lat": a.get("latitude"),
                "lng": a.get("longitude"),
            }
            for a in addrs
            if a.get("id") or a.get("addressId")
        ]

    async def search(self, session, query: str, ctx: ExecCtx) -> list[Product]:
        raw = await self._call(session, "search_products", {"query": query})
        sc = raw.get("structured") or {}
        out: list[Product] = []
        for p in sc.get("products", []) if isinstance(sc, dict) else []:
            pvid = p.get("productVariantId") or p.get("id")
            if not pvid:
                continue
            out.append(
                Product(
                    provider=self.id,
                    item_id=str(pvid),
                    name=p.get("name", query),
                    price_minor=int(p.get("price") or 0),  # already paise
                    in_stock=bool(p.get("inStock", p.get("availableQuantity") != 0)),
                    variation=p.get("packSize", ""),
                    image_url=_safe_image_url(p.get("imageUrl", "")),
                    secondary_id=str(p.get("storeProductId") or ""),
                )
            )
        return out

    async def write_cart(
        self, session, items, ctx: ExecCtx, *, replace: bool = False
    ) -> CartResult:
        cart_items = []
        for iid, qty, sid in items:
            it: dict[str, Any] = {"productVariantId": iid, "quantity": qty}
            if sid:
                it["storeProductId"] = sid
            cart_items.append(it)
        args = {
            "deviceId": ctx.device_id or f"companion-{ctx.user_id}",
            "cartItems": cart_items,
            "replaceCart": replace,
        }
        raw = await self._call(session, "update_cart", args)
        if raw.get("is_error"):
            return CartResult.failure(self.classify(raw.get("text", "")), raw.get("text", ""))
        return CartResult.success(self.parse_cart(raw))

    async def clear(self, session, ctx: ExecCtx) -> None:
        # Zepto has no clear tool, and replaceCart=[] does NOT empty the cart (verified live).
        # The deterministic clear: read the REAL cart and set every item to qty 0 — uses the
        # actual cart ids (not the session catalog), so it clears even previous-session items.
        res = await self.read_cart(session, ctx)
        items = res.cart.items if (res.ok and res.cart) else []
        if not items:
            return
        cart_items = [
            {
                "productVariantId": i.item_id,
                "quantity": 0,
                **({"storeProductId": i.secondary_id} if i.secondary_id else {}),
            }
            for i in items
        ]
        await self._call(
            session,
            "update_cart",
            {
                "deviceId": ctx.device_id or f"companion-{ctx.user_id}",
                "cartItems": cart_items,
                "replaceCart": False,
            },
        )

    async def read_cart(self, session, ctx: ExecCtx) -> CartResult:
        raw = await self._call(session, "view_cart", {})
        if raw.get("is_error"):
            kind = self.classify(raw.get("text", ""))
            return (
                CartResult.success(Cart(provider=self.id))
                if kind == ErrorKind.EMPTY_CART
                else CartResult.failure(kind, raw.get("text", ""))
            )
        return CartResult.success(self.parse_cart(raw))

    async def price_cart(self, session, ctx: ExecCtx, cart: Cart) -> None:
        # Zepto's view_cart has no fees; the FINAL total lives in get_payment_methods (read-only,
        # pre-order). It needs the store OPEN — on STORE_CLOSED / not-serviceable we flag the cart
        # so the UI can prompt an address change. POC: scripts/zepto_price_poc.py.
        if cart.is_empty:
            return
        subtotal = sum(i.price_minor * i.qty for i in cart.items)
        cart.total_minor = cart.total_minor or subtotal
        cart.bill = [{"label": "Item Total", "minor": subtotal}]
        raw = await self._call(session, "get_payment_methods", {})
        if raw.get("is_error"):
            cart.unavailable = (
                "Is address pe store abhi band ya unavailable hai — delivery nahi ho paayegi. "
                "Address badal ke try karein."
            )
            return
        m = re.search(r"Order Total:\s*₹\s*([\d,]+)", raw.get("text", ""))
        if not m:
            return
        total = int(m.group(1).replace(",", "")) * 100
        cart.total_minor = total
        cart.bill = [
            {"label": "Item Total", "minor": subtotal},
            {"label": "Delivery & charges", "minor": max(0, total - subtotal)},
        ]

    def parse_cart(self, raw: dict[str, Any]) -> Cart:
        sc = raw.get("structured") or {}
        items = [
            CartItem(
                item_id=str(i.get("productVariantId") or i.get("id")),
                name=i.get("name", ""),
                qty=int(i.get("quantity", 0)),
                price_minor=int(i.get("price") or 0),  # often null → enriched from catalog upstream
                secondary_id=str(i.get("storeProductId") or ""),
            )
            for i in (sc.get("items") or [])
        ]
        return Cart(provider=self.id, items=items, cart_id=str(sc.get("cartKey", "")))
