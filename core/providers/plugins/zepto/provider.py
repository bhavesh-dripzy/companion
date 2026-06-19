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
from core.providers.plugins.zepto.cards import build_product_cards
from core.providers.plugins.zepto.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.zepto.planner_context import build_context
from core.providers.plugins.zepto.session import ZeptoSession, merge_products

_CART_TOOLS = {"view_cart", "update_cart"}
_SEARCH_TOOLS = {"search_products", "search_multiple_products"}

_DEFAULT_URL = "https://mcp.zepto.co.in/mcp"

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
    capability_class = "grocery"
    cart_session_key = "session_zepto_cart"

    def mcp_url(self, ctx: ExecCtx) -> str | None:
        return os.getenv("ZEPTO_MCP_URL", _DEFAULT_URL)

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        return remote_stdio_spec(self.mcp_url(ctx), user_id=ctx.user_id)

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        """Guard: return needs_location immediately if no GPS — avoids opening MCP session."""
        geo = ctx.user_geo if isinstance(ctx.user_geo, dict) else {}
        lat = geo.get("latitude", geo.get("lat"))
        lng = geo.get("longitude", geo.get("lng"))
        if lat is None or lng is None:
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
        geo = ctx.user_geo if isinstance(ctx.user_geo, dict) else {}
        lat = float(geo.get("latitude", geo.get("lat")))
        lng = float(geo.get("longitude", geo.get("lng")))
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
        return None

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

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        products = merge_products(slice_.get("session_zepto_products"), result.results)
        if products:
            merged["session_zepto_products"] = products
        for tr in result.results:
            if tr.tool in _CART_TOOLS and tr.ok:
                snap = cart_snapshot(tr.data if isinstance(tr.data, dict) else {})
                if snap:
                    # Backfill image URLs from the product LRU — the cart API
                    # never returns imageUrl, but we may have seen the product
                    # during this session's search results.
                    all_products = merged.get("session_zepto_products") or []
                    img_map = {
                        p["product_variant_id"]: p.get("image_url")
                        for p in all_products
                        if p.get("product_variant_id") and p.get("image_url")
                    }
                    if img_map:
                        for item in snap.get("items") or []:
                            if not item.get("image_url"):
                                item["image_url"] = img_map.get(item.get("product_variant_id"))
                    merged["session_zepto_cart"] = snap
        return merged

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)
