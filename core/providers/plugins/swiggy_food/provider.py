"""Swiggy Food — clean `McpProvider` (second food provider, same pattern as Zomato).

The base drives the session/placeholder/execute loop; this plugin supplies the
Swiggy-specific bits:

  * `stdio_spec` — the mcp-remote launch for `mcp.swiggy.com/food` (tokens hydrated).
  * `build_attachments` — menu + restaurant cards from real `ToolResult`s (golden-tested).
  * `merge` — fold this turn's restaurants into the session LRU (shared helper).
  * `plan` — delegate to the tuned legacy domain agent (parity bridge, Phase-8-removed).

Scope: restaurant + menu *search* (read-only) are ported + validated against real
Swiggy data. Cart cards still come from the proven legacy executor (order-flow
enrichment + no safe fixture); the bootstrap swap is gated on those so `dev` never
regresses. Swiggy's dual-key address handling (addressId/address_id) is covered by
the shared placeholder resolver's `__ADDRESS_ID__` aliases.
"""

from __future__ import annotations

import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.address import merge_saved_addresses
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.coupon_cards import build_coupon_cards
from core.providers.mcp.info_cards import build_info_cards
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.mcp.result_cards import build_order_cards, build_tracking_card
from core.providers.plugins.swiggy_food.cards import build_restaurant_cards
from core.providers.plugins.swiggy_food.cart_args import (
    collect_menu_index,
    is_choice_trivial,
    normalize_cart_payload,
    pick_default_variants,
    summarize_variant_choices,
)
from core.providers.plugins.swiggy_food.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.swiggy_food.digest import summarize
from core.providers.plugins.swiggy_food.menu_cards import build_menu_cards
from core.providers.plugins.swiggy_food.placeholders import SwiggyFoodResolver
from core.providers.plugins.swiggy_food.planner_context import build_context
from core.providers.plugins.swiggy_food.session import (
    SwiggyFoodSession,
    merge_menu,
    merge_restaurants,
)

_CART_TOOLS = {"get_food_cart", "update_food_cart"}
_MENU_INDEX_TOOLS = {"search_menu", "get_restaurant_menu"}
_SCRATCH_MENU_INDEX = "swiggy_menu_index"

_DEFAULT_URL = "https://mcp.swiggy.com/food"


class SwiggyFoodProvider(McpProvider):
    id = "swiggy_food"
    label = "Swiggy Food MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = SwiggyFoodSession
    saved_addresses_tool = "get_addresses"
    resolver = SwiggyFoodResolver()

    def mcp_url(self, ctx: ExecCtx) -> str | None:
        return os.getenv("SWIGGY_FOOD_MCP_URL", _DEFAULT_URL)

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        return remote_stdio_spec(self.mcp_url(ctx), user_id=ctx.user_id)

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Coerce cart payloads to Swiggy's strict request shape — the planner
        copies the nested camelCase RESPONSE shape, which the MCP rejects."""
        if tool == "update_food_cart":
            return normalize_cart_payload(args)
        return args

    def harvest(self, tool: str, envelope: dict[str, Any], ctx: ExecCtx) -> None:
        """Index this plan's menu envelopes so a same-plan cart add can resolve
        variant requirements without a second round trip."""
        if tool in _MENU_INDEX_TOOLS:
            ctx.scratch.setdefault(_SCRATCH_MENU_INDEX, {}).update(collect_menu_index(envelope))

    def _variant_meta(self, item_id: str, ctx: ExecCtx) -> dict[str, Any] | None:
        """Variant schema for an item: this plan's menu index, else the session
        menu rows carried from earlier turns."""
        meta = (ctx.scratch.get(_SCRATCH_MENU_INDEX) or {}).get(item_id)
        if meta:
            return meta
        menu = ctx.session.get("session_menu") if isinstance(ctx.session, dict) else None
        for row in menu if isinstance(menu, list) else []:
            if not isinstance(row, dict) or str(row.get("provider") or self.id) != self.id:
                continue
            rid = str(row.get("menu_item_id") or row.get("variant_id") or "").strip()
            if rid == item_id and (row.get("hasVariants") or row.get("variantsV2")):
                return {
                    "name": row.get("item_name"),
                    "hasVariants": bool(row.get("hasVariants") or row.get("variantsV2")),
                    "variantsV2": row.get("variantsV2") or [],
                }
        return None

    def before_call(self, tool: str, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        """Variant gate for cart adds (port of the legacy enrichment): a row
        targeting a multi-variant item with no explicit pick either gets the
        trivial single option injected, or the call is SKIPPED and the choices
        are returned so the narrator asks instead of pinning a guessed size."""
        if tool != "update_food_cart" or not isinstance(args, dict):
            return None
        pending: list[dict[str, Any]] = []
        for item in args.get("cartItems") or []:
            if not isinstance(item, dict):
                continue
            if item.get("variantsV2") or item.get("variants") or item.get("variations"):
                continue
            iid = str(item.get("menu_item_id") or "").strip()
            meta = self._variant_meta(iid, ctx) if iid else None
            if not meta or not meta.get("hasVariants"):
                continue
            v2 = meta.get("variantsV2") or []
            if is_choice_trivial(v2):
                picked = pick_default_variants(v2)
                if picked:
                    item["variantsV2"] = picked
            else:
                pending.append(summarize_variant_choices(iid, meta.get("name"), v2))
        if not pending:
            return None
        return {
            "is_error": False,
            "data": {
                "variant_choice_required": pending,
                "message": (
                    "Cart NOT updated: these items need a size/variant choice. "
                    "Ask the user to pick one option per group."
                ),
            },
        }

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        # Internal render order: menu -> restaurant -> cart -> orders/tracking/coupons.
        return [
            *build_menu_cards(result.results),
            *build_restaurant_cards(result.results),
            *build_cart_cards(result.results),
            *build_order_cards(
                result.results,
                tools={"get_food_orders", "get_food_order_details"},
                provider_id=self.id,
                brand="swiggy",
            ),
            *build_tracking_card(
                result.results, tools={"track_food_order"}, provider_id=self.id, brand="swiggy"
            ),
            *build_coupon_cards(
                result.results,
                tools={"fetch_food_coupons", "apply_food_coupon"},
                provider_id=self.id,
                brand="swiggy",
            ),
            *build_info_cards(
                result.results,
                exclude={
                    "search_restaurants",
                    "search_menu",
                    "get_restaurant_menu",
                    "get_food_cart",
                    "update_food_cart",
                    "get_food_orders",
                    "get_food_order_details",
                    "track_food_order",
                    "fetch_food_coupons",
                    "apply_food_coupon",
                    "get_addresses",
                    "report_error",
                },
                provider_id=self.id,
                brand="swiggy",
            ),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        merge_saved_addresses(merged, result.results, provider=self.id)
        restaurants = merge_restaurants(slice_.get("session_restaurants"), result.results)
        if restaurants:
            merged["session_restaurants"] = restaurants
        menu = merge_menu(slice_.get("session_menu"), result.results)
        if menu:
            merged["session_menu"] = menu
        # Single-restaurant replacement cart: the latest cart envelope wins.
        for tr in result.results:
            if tr.tool in _CART_TOOLS and tr.ok:
                snap = cart_snapshot(
                    tr.data if isinstance(tr.data, dict) else {}, args=tr.arguments
                )
                if snap:
                    merged["session_cart"] = snap
        return merged

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)

    def summarize_result(self, tool_result: Any) -> str | None:
        data = tool_result.data if isinstance(tool_result.data, dict) else {}
        return summarize(tool_result.tool, data) or super().summarize_result(tool_result)
