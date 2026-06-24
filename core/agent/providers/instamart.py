"""Swiggy Instamart adapter (grocery). REPLACE-ALL, fragile cart; prices in rupees.

Tools: search_products / update_cart / get_cart. Item id = spinId. See swiggy_bhavesh.md.
"""

from __future__ import annotations

from typing import Any

from core.agent.contracts import Cart, CartItem, CartResult, ErrorKind, Product, rupees_to_minor
from core.agent.providers.base import CARTABLE, CHECKOUTABLE, SEARCHABLE, ExecCtx, ProviderAdapter


class InstamartProvider(ProviderAdapter):
    id = "instamart"
    domains = ("grocery",)
    mcp_url = "https://mcp.swiggy.com/im"
    capabilities = frozenset({SEARCHABLE, CARTABLE, CHECKOUTABLE})
    cart_strategy = "replace_all_fragile"
    free_delivery_minor = 9900  # ₹99 minimum order for free delivery

    async def default_context(self, session, user_id: str) -> dict[str, Any]:
        addrs = await self.list_addresses(session)
        if not addrs:
            return {}
        # prefer a Gurugram address (generally serviceable), else the first saved one
        pick = next((a for a in addrs if "gurugram" in a["label"].lower()), addrs[0])
        return {"address_id": pick["id"]}

    async def list_addresses(self, session) -> list[dict[str, Any]]:
        raw = await self._call(session, "get_addresses", {})
        addrs = (raw.get("structured") or {}).get("addresses") or []
        # Instamart addresses carry no coordinates; label = tag + line.
        return [
            {
                "id": a.get("id"),
                "label": " · ".join(x for x in (a.get("addressTag"), a.get("addressLine")) if x)
                or a.get("addressLine", ""),
                "lat": None,
                "lng": None,
            }
            for a in addrs
            if a.get("id")
        ]

    async def search(self, session: Any, query: str, ctx: ExecCtx) -> list[Product]:
        raw = await self._call(
            session, "search_products", {"query": query, "addressId": ctx.address_id}
        )
        sc = raw.get("structured") or {}
        out: list[Product] = []
        for p in sc.get("products", []) if isinstance(sc, dict) else []:
            brand = p.get("brand", "")
            for v in p.get("variations", []):
                spin = v.get("spinId")
                if not spin:
                    continue
                price = v.get("price") or {}
                # parentProductId groups all sizes of one product (e.g. Harvest Gold White Bread
                # 350g + 700g share a parent) → used to render one card per product with a picker.
                parent = p.get("parentProductId") or p.get("productId") or str(spin)
                out.append(
                    Product(
                        provider=self.id,
                        item_id=str(spin),
                        name=v.get("displayName") or p.get("displayName") or query,
                        price_minor=rupees_to_minor(price.get("offerPrice", price.get("mrp"))),
                        in_stock=bool(v.get("isInStockAndAvailable", p.get("inStock", True))),
                        brand=brand,
                        variation=v.get("quantityDescription", ""),
                        image_url=v.get("imageUrl", ""),
                        extra={"parent_product_id": str(parent)},
                    )
                )
        return out

    async def write_cart(self, session, items, ctx: ExecCtx, *, replace: bool = True) -> CartResult:
        # Instamart is inherently REPLACE-ALL — `items` is always the full desired set.
        args = {
            "items": [{"spinId": iid, "quantity": qty} for (iid, qty, _sid) in items],
            "selectedAddressId": ctx.address_id,
        }
        raw = await self._call(session, "update_cart", args)
        if raw.get("is_error"):
            return CartResult.failure(self.classify(raw.get("text", "")), raw.get("text", ""))
        return CartResult.success(self.parse_cart(raw))

    async def clear(self, session, ctx: ExecCtx) -> None:
        await self._call(session, "clear_cart", {})

    async def read_cart(self, session, ctx: ExecCtx) -> CartResult:
        raw = await self._call(session, "get_cart", {})
        if raw.get("is_error"):
            kind = self.classify(raw.get("text", ""))
            if kind == ErrorKind.EMPTY_CART:
                return CartResult.success(Cart(provider=self.id))  # empty == clean state
            return CartResult.failure(kind, raw.get("text", ""))
        return CartResult.success(self.parse_cart(raw))

    def parse_cart(self, raw: dict[str, Any]) -> Cart:
        sc = raw.get("structured") or {}
        items = [
            CartItem(
                item_id=str(i.get("spinId")),
                name=i.get("itemName", ""),
                qty=int(i.get("quantity", 0)),
                price_minor=rupees_to_minor(i.get("discountedFinalPrice", i.get("mrp"))),
            )
            for i in (sc.get("items") or [])
        ]
        bill = [
            {"label": li.get("label", ""), "minor": rupees_to_minor(li.get("value"))}
            for li in ((sc.get("billBreakdown") or {}).get("lineItems") or [])
        ]
        return Cart(
            provider=self.id,
            items=items,
            total_minor=rupees_to_minor(sc.get("cartTotalAmount")),
            bill=bill,
            cart_id=str(sc.get("cartId", "")),
        )
