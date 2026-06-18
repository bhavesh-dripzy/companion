"""Zepto — clean `McpProvider` (grocery; structured product search + LRU).

  * `stdio_spec` — mcp-remote launch for `mcp.zepto.co.in` (tokens hydrated).
  * `build_attachments` — zepto_product_cards from real `ToolResult`s.
  * `merge` — fold this turn's products into the productVariantId LRU.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge).

Scope: product *search* (read-only) is ported + validated against real Zepto data.
Zepto requires a store to be selected first (get_location_serviceability /
select_store); the legacy domain planner handles that setup chain, which the
parity bridge reuses. Cart cards stay on the executor path; bootstrap swap gated.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ProviderResult
from core.middleware.errors import NeedsLocationError, result_from_exception
from core.providers.base import ExecCtx
from core.providers.mcp.connection import StdioSpec, call_tool
from core.providers.mcp.info_cards import build_info_cards
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.placeholders import DEFAULT_ALIASES, DEFAULT_ISSUERS, PlaceholderResolver
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.mcp.result_cards import build_order_cards
from core.providers.plugins.zepto.cards import build_product_cards, products_from_search
from core.providers.plugins.zepto.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.zepto.planner_context import build_context
from core.providers.plugins.zepto.session import ZeptoSession, merge_products

_CART_TOOLS = {"view_cart", "update_cart"}
_SEARCH_TOOLS = {"search_products", "search_multiple_products"}

_DEFAULT_URL = "https://mcp.zepto.co.in/mcp"
# The committed Zepto delivery location {latitude, longitude, ...} — pinned by
# companion_api at the request boundary. Authoritative for the store the cart
# binds to (we don't let ambient GPS drift the store mid-session).
_SESSION_ZEPTO_LOCATION = "selected_zepto_location"

# pvid: <uuid>, spid: <uuid> footer that Zepto appends to prose product/cart
# replies — a cheap fallback id source when a reply lacks structuredContent.
_PVID_SPID_RE = re.compile(r"pvid:\s*([0-9a-f-]{36})\s*,\s*spid:\s*([0-9a-f-]{36})", re.IGNORECASE)

_ZEPTO_ALIASES = {
    **DEFAULT_ALIASES,
    # Learned from search_products / search_multiple_products structured response.
    # First product in the result list becomes the "active" pvid/spid for in-plan
    # chaining (e.g. search then immediately update_cart).
    "__ZEPTO_PVID__": ("productVariantId", "product_variant_id", "variantId"),
    "__ZEPTO_SPID__": ("storeProductId", "store_product_id"),
}

_ZEPTO_ISSUERS = {
    **DEFAULT_ISSUERS,
    # A new search invalidates any pvid/spid carried from a prior search in this plan.
    "search_products": ("__ZEPTO_PVID__", "__ZEPTO_SPID__"),
    "search_multiple_products": ("__ZEPTO_PVID__", "__ZEPTO_SPID__"),
}


class _ZeptoResolver(PlaceholderResolver):
    # pvid/spid are product-search-scoped — don't persist them across turns.
    transient: frozenset[str] = frozenset({"__ZEPTO_PVID__", "__ZEPTO_SPID__"})


class ZeptoProvider(McpProvider):
    resolver: PlaceholderResolver = _ZeptoResolver(aliases=_ZEPTO_ALIASES, issuers=_ZEPTO_ISSUERS)
    id = "zepto"
    label = "Zepto MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = ZeptoSession

    def mcp_url(self, ctx: ExecCtx) -> str | None:
        return os.getenv("ZEPTO_MCP_URL", _DEFAULT_URL)

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        return remote_stdio_spec(self.mcp_url(ctx), user_id=ctx.user_id)

    @staticmethod
    def _effective_geo(ctx: ExecCtx) -> tuple[float, float] | None:
        """The coordinate Zepto should resolve its store from.

        The user's SAVED location (committed during onboarding, carried in the
        session) wins over ambient GPS — the cart binds to one store and we
        don't want a phone that moved to silently switch stores and invalidate
        the cart. Falls back to live ``ctx.user_geo`` when nothing is saved."""
        session = ctx.session if isinstance(ctx.session, dict) else {}
        saved = session.get(_SESSION_ZEPTO_LOCATION)
        for src in (saved, ctx.user_geo if isinstance(ctx.user_geo, dict) else None):
            if not isinstance(src, dict):
                continue
            try:
                lat = float(src.get("latitude", src.get("lat")))
                lng = float(src.get("longitude", src.get("lng")))
            except (TypeError, ValueError):
                continue
            return lat, lng
        return None

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        """Guard: return needs_location immediately if no GPS — avoids opening MCP session."""
        if self._effective_geo(ctx) is None:
            exc = NeedsLocationError("User location required to find your Zepto store.")
            return ProviderResult(
                provider=self.id,
                results=[
                    result_from_exception(c.tool, self.id, exc, arguments=c.arguments)
                    for c in calls
                ],
                ok=False,
                error=ErrorCode.NEEDS_LOCATION,
                error_detail=str(exc),
            )
        return await super().run(calls, ctx)

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        await super().before_run(session, ctx)
        geo = self._effective_geo(ctx)
        if geo is None:  # pragma: no cover - run() already guarded this
            return
        lat, lng = geo
        ctx.placeholders["__USER_LAT__"] = lat
        ctx.placeholders["__USER_LNG__"] = lng
        # Stable per-user device ID required by update_cart.
        if "__ZEPTO_DEVICE_ID__" not in ctx.placeholders:
            ctx.placeholders["__ZEPTO_DEVICE_ID__"] = str(
                uuid.uuid5(uuid.NAMESPACE_DNS, f"zepto-device-{ctx.user_id}")
            )
        # Proactively register the store for this MCP session so the planner's
        # get_location_serviceability step is optional (each session is stateless).
        try:
            await call_tool(
                session, "get_location_serviceability", {"latitude": lat, "longitude": lng}
            )
            ctx.scratch["zepto_store_registered"] = True
        except Exception:
            pass  # planner-emitted serviceability call will be the fallback

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # Zepto's pageNumber is 0-indexed; planners routinely emit pageNumber:1
        # thinking it means "first page". Drop it so the server uses the default (0).
        if tool in {"search_products", "search_multiple_products"} and args.get("pageNumber") == 1:
            return {k: v for k, v in args.items() if k != "pageNumber"}
        return args

    def before_call(self, tool: str, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        # Short-circuit the planner's serviceability call when already done proactively.
        if tool == "get_location_serviceability" and ctx.scratch.get("zepto_store_registered"):
            return {
                "is_error": False,
                "data": {"raw_text": "Store already registered for this session."},
            }
        if tool == "update_cart":
            self._reconcile_cart_items(args, ctx)
        return None

    def harvest(self, tool: str, envelope: dict[str, Any], ctx: ExecCtx) -> None:
        # Record the (pvid -> spid) pairs returned by a search so a later
        # update_cart in the same plan can be validated against real,
        # store-correct ids — and so a planner-supplied spid that doesn't match
        # the pvid gets repaired. Zepto rejects/ misfires on a pvid that isn't a
        # real product at the selected store, and the planner sometimes invents
        # one; we only forward ids drawn from a real search/cart.
        if tool not in _SEARCH_TOOLS or not isinstance(envelope, dict) or envelope.get("is_error"):
            return
        valid: dict[str, str] = ctx.scratch.setdefault("_zepto_valid_ids", {})
        for row in products_from_search(envelope, query=None):
            pvid = str(row.get("product_variant_id") or "").strip()
            spid = str(row.get("store_product_id") or "").strip()
            if pvid and spid:
                valid[pvid] = spid
        # Prose fallback: a text-only reply still carries `pvid: …, spid: …`.
        data = envelope.get("data")
        if isinstance(data, str):
            for pvid, spid in _PVID_SPID_RE.findall(data):
                valid[pvid.lower()] = spid.lower()

    def _valid_ids(self, ctx: ExecCtx) -> dict[str, str]:
        """pvid -> spid for products known-valid at the current store: this
        turn's searches (scratch) plus the carried products LRU plus cart items."""
        valid: dict[str, str] = dict(ctx.scratch.get("_zepto_valid_ids") or {})
        session = ctx.session if isinstance(ctx.session, dict) else {}
        for prod in session.get("session_zepto_products") or []:
            if not isinstance(prod, dict):
                continue
            pvid = str(prod.get("product_variant_id") or "").strip()
            spid = str(prod.get("store_product_id") or "").strip()
            if pvid and spid:
                valid.setdefault(pvid, spid)
        cart = session.get("session_zepto_cart")
        for it in (cart.get("items") if isinstance(cart, dict) else None) or []:
            if not isinstance(it, dict):
                continue
            pvid = str(it.get("product_variant_id") or "").strip()
            spid = str(it.get("store_product_id") or "").strip()
            if pvid and spid:
                valid.setdefault(pvid, spid)
        return valid

    def _reconcile_cart_items(self, args: dict[str, Any], ctx: ExecCtx) -> None:
        """Harden a planner-built update_cart payload.

        * deviceId is forced to the stable per-user id (the planner can't know
          it, and an omitted/wrong one orphans the cart).
        * replaceCart is forced OFF — Zepto's default is a safe MERGE/upsert, so
          a partial item list never wipes the rest of the cart. Removal is done
          per-item with quantity=0 (verified: clean removal, others preserved).
        * Every add/update item's spid is set from the validated (pvid -> spid)
          map; a pvid we've never seen at this store is dropped (fabricated id).
          quantity=0 removals pass through (harmless even for unknown ids)."""
        args["deviceId"] = ctx.placeholders.get("__ZEPTO_DEVICE_ID__") or args.get("deviceId")
        args.pop("replaceCart", None)
        valid = self._valid_ids(ctx)
        out: list[dict[str, Any]] = []
        for it in args.get("cartItems") or []:
            if not isinstance(it, dict):
                continue
            pvid = str(it.get("productVariantId") or it.get("product_variant_id") or "").strip()
            if not pvid:
                continue
            try:
                qty = int(it.get("quantity"))
            except (TypeError, ValueError):
                qty = 1
            spid = (
                valid.get(pvid)
                or str(it.get("storeProductId") or it.get("store_product_id") or "").strip()
            )
            if qty <= 0:
                # Removal — keep it through even if the id is unknown (no-op then).
                out.append({"productVariantId": pvid, "storeProductId": spid, "quantity": 0})
                continue
            if valid and pvid not in valid:
                # Drop a pvid the planner invented (not from a real search at this
                # store) — skipping beats a wrong/failed add.
                continue
            item = {"productVariantId": pvid, "storeProductId": spid, "quantity": qty}
            for k in ("name", "price", "mrp", "imageUrl", "packSize"):
                if it.get(k) is not None:
                    item[k] = it[k]
            out.append(item)
        if out:
            args["cartItems"] = out

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return [
            *build_product_cards(result.results),
            *build_cart_cards(result.results),
            *build_order_cards(
                result.results,
                tools={"list_order_history", "get_order_detail"},
                provider_id=self.id,
                brand="zepto",
                paise=True,
            ),
            *build_info_cards(
                result.results,
                exclude={
                    "get_location_serviceability",
                    "search_products",
                    "search_multiple_products",
                    "get_past_order_items",
                    "view_cart",
                    "update_cart",
                    "list_order_history",
                    "get_order_detail",
                    "list_saved_addresses",
                },
                provider_id=self.id,
                brand="zepto",
            ),
        ]

    @staticmethod
    def _cart_is_empty(envelope: dict[str, Any]) -> bool:
        """True when a cart envelope explicitly reports an EMPTY cart (structured
        isEmpty / empty items list, or prose 'cart is empty') — distinct from a
        reply that simply carried no cart data."""
        data = envelope.get("data")
        if isinstance(data, dict):
            if data.get("isEmpty") is True or data.get("is_empty") is True:
                return True
            items = data.get("items", data.get("cartItems"))
            if isinstance(items, list) and not items:
                return True
        return isinstance(data, str) and "cart is empty" in data.lower()

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        products = merge_products(slice_.get("session_zepto_products"), result.results)
        if products:
            merged["session_zepto_products"] = products
        for tr in result.results:
            if tr.tool not in _CART_TOOLS or not tr.ok:
                continue
            env = tr.data if isinstance(tr.data, dict) else {}
            snap = cart_snapshot(env)
            if snap is None:
                # A view_cart/update_cart that returns an explicitly EMPTY cart
                # must clear the session snapshot — otherwise a just-cleared cart
                # lingers as stale items ("you cleared it" but it still shows up).
                if self._cart_is_empty(env):
                    merged["session_zepto_cart"] = {"is_empty": True, "items": []}
                continue
            # Backfill name / price / pack size / image from the product LRU — the
            # cart payload often omits the name (shows blank), which breaks "change
            # the milk to 3" (the planner matches a cart line by name). We saw the
            # product during this session's searches, so key by pvid and fill any
            # missing display field.
            prod_map = {
                p["product_variant_id"]: p
                for p in (merged.get("session_zepto_products") or [])
                if isinstance(p, dict) and p.get("product_variant_id")
            }
            for item in snap.get("items") or []:
                src = prod_map.get(item.get("product_variant_id"))
                if not src:
                    continue
                for fld in ("name", "price_inr", "price_text", "pack_size", "image_url"):
                    if not item.get(fld) and src.get(fld) is not None:
                        item[fld] = src[fld]
            merged["session_zepto_cart"] = snap
        return merged

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)
