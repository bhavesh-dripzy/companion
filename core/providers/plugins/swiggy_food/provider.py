"""Swiggy Food — clean `McpProvider` (second food provider, same pattern as Zomato).

The base drives the session/placeholder/execute loop; this plugin supplies the
Swiggy-specific bits:

  * `stdio_spec` — the mcp-remote launch for `mcp.swiggy.com/food` (tokens hydrated).
  * `build_attachments` — menu + restaurant cards from real `ToolResult`s (golden-tested).
  * `merge` — fold this turn's restaurants into the session LRU (shared helper).
  * `plan` — delegate to the tuned legacy domain agent (parity bridge, Phase-8-removed).

Cart/address robustness mirrors Swiggy Instamart (and Zepto): a committed delivery
address anchors every call, `update_food_cart` is folded into an UPSERT in code
(Swiggy replaces the whole cart, verified live), a failed mutation is rolled back
from the live snapshot, and planner-invented item ids are dropped before they reach
Swiggy. Swiggy's dual-key address handling is covered by the shared resolver's
`__ADDRESS_ID__` aliases.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.selectable import Selectable
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.address import extract_address_rows, merge_saved_addresses
from core.providers.mcp.connection import StdioSpec, call_tool
from core.providers.mcp.coupon_cards import build_coupon_cards
from core.providers.mcp.info_cards import build_info_cards
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.mcp.result_cards import build_order_cards, build_tracking_card
from core.providers.mcp.result_parse import tool_result_payload
from core.providers.plugins.swiggy_food.cards import build_restaurant_cards
from core.providers.plugins.swiggy_food.cart_args import (
    collect_item_ids,
    collect_menu_index,
    is_choice_trivial,
    normalize_cart_payload,
    pick_default_variants,
    summarize_variant_choices,
)
from core.providers.plugins.swiggy_food.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.swiggy_food.cart_reconcile import (
    reconcile_cart_items,
    request_rows_from_cart,
    variant_rows_valid,
)
from core.providers.plugins.swiggy_food.digest import summarize
from core.providers.plugins.swiggy_food.menu_cards import build_menu_cards
from core.providers.plugins.swiggy_food.placeholders import SwiggyFoodResolver
from core.providers.plugins.swiggy_food.planner_context import build_context
from core.providers.plugins.swiggy_food.session import (
    SwiggyFoodSession,
    merge_menu,
    merge_restaurants,
)

logger = logging.getLogger(__name__)

_CART_TOOLS = {"get_food_cart", "update_food_cart"}
_MENU_INDEX_TOOLS = {"search_menu", "get_restaurant_menu"}
# Tools that CHANGE the cart — before_run reads the live cart ahead of them so the
# upsert reconcile folds onto the REAL current lines, not a stale session snapshot.
_MUTATING_CART_TOOLS = {"update_food_cart"}
_SCRATCH_MENU_INDEX = "swiggy_menu_index"

# Tools that REQUIRE a delivery-address arg. The executor guarantees the committed
# address is present so the planner can never break a call by omitting it.
_ADDRESS_ARG = {
    "search_restaurants": "addressId",
    "search_menu": "addressId",
    "get_restaurant_menu": "addressId",
    "get_food_cart": "addressId",
    "update_food_cart": "addressId",
    "place_food_order": "addressId",
}

_DEFAULT_URL = "https://mcp.swiggy.com/food"
# The committed Swiggy Food delivery address — pinned by companion_api at the
# request boundary (DB source of truth) and re-committed by merge on a switch.
_SESSION_SELECTED_ADDRESS = "selected_swiggy_food_address_id"


class SwiggyFoodProvider(McpProvider):
    id = "swiggy_food"
    label = "Swiggy Food MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = SwiggyFoodSession
    capability_class = "food"
    cart_session_key = "session_cart"
    saved_addresses_tool = "get_addresses"
    resolver = SwiggyFoodResolver()
    selectables = (
        Selectable(
            kind="delivery_address",
            saved_key="session_addresses",
            committed_key=_SESSION_SELECTED_ADDRESS,
            id_field="address_id",
            label_fields=("addressTag", "address_tag", "label", "location_name"),
        ),
    )

    def mcp_url(self, ctx: ExecCtx) -> str | None:
        return os.getenv("SWIGGY_FOOD_MCP_URL", _DEFAULT_URL)

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        return remote_stdio_spec(self.mcp_url(ctx), user_id=ctx.user_id)

    # ----------------------------------------------------------------- address
    @staticmethod
    def _anchor_address(ctx: ExecCtx) -> str | None:
        """The committed delivery address, authoritative for every Swiggy Food call.

        The user's EXPLICIT selection (`selected_swiggy_food_address_id` — set by the
        UI address picker, the saved default source-of-truth, or seeded from the
        address resolved on the first turn) WINS: it overrides the planner's per-call
        addressId (which drifts to the first saved address), and lets an explicit
        address SWITCH take effect mid-session (mirrors Instamart)."""
        session = ctx.session if isinstance(ctx.session, dict) else {}
        return str(session.get(_SESSION_SELECTED_ADDRESS) or "").strip() or None

    @staticmethod
    def _resolved_address(ctx: ExecCtx) -> str | None:
        placeholders = ctx.placeholders if isinstance(ctx.placeholders, dict) else {}
        for key in ("__ADDRESS_ID__", "swiggy_food:__ADDRESS_ID__"):
            val = str(placeholders.get(key) or "").strip()
            if val and not val.startswith("__"):
                return val
        scratch = ctx.scratch if isinstance(ctx.scratch, dict) else {}
        val = str(scratch.get("resolved_address_id") or "").strip()
        if val:
            return val
        return SwiggyFoodProvider._anchor_address(ctx)

    @staticmethod
    def _is_saved_address(address_id: str, ctx: ExecCtx) -> bool:
        """True when ``address_id`` is one of the user's saved Swiggy addresses.

        Guards the switch path: the planner only emits a concrete addressId by
        copying it from the saved-addresses block, so a value that isn't a known
        saved id is a fabrication and must not override the committed anchor."""
        target = (address_id or "").strip()
        if not target:
            return False
        candidates: list[Any] = []
        rows = ctx.scratch.get("saved_address_rows") if isinstance(ctx.scratch, dict) else None
        if isinstance(rows, list):
            candidates.extend(rows)
        session = ctx.session if isinstance(ctx.session, dict) else {}
        carried = session.get("session_addresses")
        if isinstance(carried, list):
            candidates.extend(carried)
        return any(
            isinstance(r, dict) and str(r.get("address_id") or r.get("id") or "").strip() == target
            for r in candidates
        )

    def _ensure_address(self, args: dict[str, Any], key: str, ctx: ExecCtx) -> None:
        """Set the address arg to the committed anchor — unless the planner is
        performing an explicit SWITCH to a different, known-saved address (then that
        wins and is recorded so merge re-commits it). With no anchor yet, fill only
        when the planner left it missing/blank (mirrors Instamart `_ensure_address`)."""
        anchor = self._anchor_address(ctx)
        planned = str(args.get(key) or "").strip()
        if (
            planned
            and not planned.startswith("__")
            and planned != anchor
            and self._is_saved_address(planned, ctx)
        ):
            ctx.scratch["swiggy_food_switched_address_id"] = planned
            args[key] = planned
            return
        if anchor:
            args[key] = anchor
            return
        if planned and not planned.startswith("__"):
            return
        resolved = self._resolved_address(ctx)
        if resolved:
            args[key] = resolved

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        # Pin the committed delivery address before the base resolves __ADDRESS_ID__,
        # otherwise the base picks the nearest saved address each turn (which may be
        # the wrong store) and the planner can drift it per call.
        anchor = self._anchor_address(ctx)
        if anchor and "__ADDRESS_ID__" not in ctx.placeholders:
            ctx.placeholders["__ADDRESS_ID__"] = anchor
            ctx.scratch["resolved_address_id"] = anchor
        await super().before_run(session, ctx)
        # Make sure the saved-address list reaches the planner + the selectable
        # switch even when the committed anchor short-circuited the base fetch above.
        # Without it, "deliver to Room" can't map to an addressId and session_addresses
        # never persists, so a typed address switch is impossible (mirrors Instamart).
        if not ctx.scratch.get("saved_address_rows") and self.saved_addresses_tool:
            try:
                raw = await call_tool(session, self.saved_addresses_tool, {})
                rows = extract_address_rows(tool_result_payload(raw))
                if rows:
                    ctx.scratch["saved_address_rows"] = rows
            except Exception:
                pass  # never block a turn on the address-list fetch
        # On a mutating turn, read the cart's REAL current lines now so the upsert
        # reconcile folds onto them (not the lossy session snapshot, which omits the
        # variant/addon request rows needed to re-send an existing line).
        if ctx.scratch.get("_swiggy_food_mutating") and "live_cart_rows" not in ctx.scratch:
            addr = self._resolved_address(ctx)
            if addr:
                try:
                    env = tool_result_payload(
                        await call_tool(session, "get_food_cart", {"addressId": addr})
                    )
                    ctx.scratch["live_cart_rows"] = request_rows_from_cart(env)
                except Exception:
                    logger.warning("swiggy_food live-cart probe FAILED", exc_info=True)

    async def run(self, calls: list, ctx: ExecCtx) -> ProviderResult:
        # Flag a cart-mutating turn so before_run reads the live cart ahead of it.
        if any(getattr(c, "tool", None) in _MUTATING_CART_TOOLS for c in calls):
            ctx.scratch["_swiggy_food_mutating"] = True
        result = await super().run(calls, ctx)
        # Surface the saved-address list + resolved/committed/switched address (from
        # before_run / before_call) for the UI picker and the "Delivering to" bar.
        rows = ctx.scratch.get("saved_address_rows")
        if isinstance(rows, list) and rows:
            result.diagnostics["saved_address_rows"] = rows
        resolved = self._used_address_id(result.results) or ctx.scratch.get("resolved_address_id")
        if resolved:
            result.diagnostics["resolved_address_id"] = resolved
        switched = ctx.scratch.get("swiggy_food_switched_address_id")
        if switched:
            result.diagnostics["switched_address_id"] = switched
        await self._restore_cart_if_poisoned(result, ctx)
        return result

    @staticmethod
    def _used_address_id(results: list) -> str | None:
        """The concrete addressId a search/cart call ran against this turn, if any."""
        for tr in results:
            if tr.tool in _ADDRESS_ARG and tr.ok:
                aid = str((tr.arguments or {}).get("addressId") or "").strip()
                if aid and not aid.startswith("__"):
                    return aid
        return None

    async def _restore_cart_if_poisoned(self, result: ProviderResult, ctx: ExecCtx) -> None:
        """A failed ``update_food_cart`` REPLACES then validates server-side, so a
        bad item can leave the cart in a broken state. Re-send the prior valid cart
        (the live snapshot from before_run) so it returns to a readable state for the
        next turn. No-op when the update succeeded or there's no snapshot to restore."""
        if not any(tr.tool == "update_food_cart" and not tr.ok for tr in result.results):
            return
        scratch = ctx.scratch if isinstance(ctx.scratch, dict) else {}
        rows = scratch.get("live_cart_rows")
        addr = self._resolved_address(ctx)
        if not isinstance(rows, list) or not rows or not addr:
            return
        res_id = rname = None
        for tr in result.results:
            if tr.tool == "update_food_cart":
                res_id = str((tr.arguments or {}).get("restaurantId") or "").strip() or None
                rname = str((tr.arguments or {}).get("restaurantName") or "").strip() or None
                break
        if not res_id:
            return
        args: dict[str, Any] = {"restaurantId": res_id, "addressId": addr, "cartItems": rows}
        if rname:
            args["restaurantName"] = rname
        try:
            async with self.open_session(ctx) as session:
                await call_tool(session, "update_food_cart", args)
        except Exception:
            logger.warning("swiggy_food cart restore FAILED", exc_info=True)

    # ----------------------------------------------------------------- planning
    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Coerce cart payloads to Swiggy's strict request shape — the planner
        copies the nested camelCase RESPONSE shape, which the MCP rejects."""
        if tool == "update_food_cart":
            return normalize_cart_payload(args)
        return args

    def harvest(self, tool: str, envelope: dict[str, Any], ctx: ExecCtx) -> None:
        """Index this plan's menu envelopes (variant resolution) and record every
        item id shown so a same-plan cart add can be validated against real,
        store-correct ids."""
        if tool in _MENU_INDEX_TOOLS:
            ctx.scratch.setdefault(_SCRATCH_MENU_INDEX, {}).update(collect_menu_index(envelope))
            ctx.scratch.setdefault("_swiggy_valid_items", set()).update(collect_item_ids(envelope))
        # A flush earlier in THIS plan means the cart is gone — a later
        # update_food_cart must rebuild from scratch, not fold onto stale items.
        if (
            tool == "flush_food_cart"
            and isinstance(envelope, dict)
            and not envelope.get("is_error")
        ):
            ctx.scratch["_swiggy_food_cart_flushed"] = True

    def _valid_item_ids(self, ctx: ExecCtx) -> set[str]:
        """menu_item_ids known-valid for the current restaurant: this turn's menu
        envelopes (scratch) plus the carried menu LRU plus items already in cart."""
        valid: set[str] = set(ctx.scratch.get("_swiggy_valid_items") or set())
        session = ctx.session if isinstance(ctx.session, dict) else {}
        for row in session.get("session_menu") or []:
            if not isinstance(row, dict):
                continue
            iid = str(row.get("menu_item_id") or row.get("variant_id") or "").strip()
            if iid:
                valid.add(iid)
        cart = session.get("session_cart")
        for it in (cart.get("items") if isinstance(cart, dict) else None) or []:
            iid = str((it or {}).get("menu_item_id") or "").strip()
            if iid:
                valid.add(iid)
        return valid

    @staticmethod
    def _cart_item_ids(ctx: ExecCtx) -> set[str]:
        """menu_item_ids already in the cart (live read this turn, else session
        snapshot) — these already carry a chosen variant, so the variant gate must
        not re-prompt on a bare quantity change."""
        ids: set[str] = set()
        scratch = ctx.scratch if isinstance(ctx.scratch, dict) else {}
        for row in scratch.get("live_cart_rows") or []:
            iid = str((row or {}).get("menu_item_id") or "").strip()
            if iid:
                ids.add(iid)
        session = ctx.session if isinstance(ctx.session, dict) else {}
        cart = session.get("session_cart")
        for it in (cart.get("items") if isinstance(cart, dict) else None) or []:
            iid = str((it or {}).get("menu_item_id") or "").strip()
            if iid:
                ids.add(iid)
        return ids

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

    def _variant_gate(self, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        """Variant gate for cart adds (port of the legacy enrichment): a row
        targeting a multi-variant item with no explicit pick either gets the trivial
        single option injected, or the call is SKIPPED and the choices returned so
        the narrator asks instead of pinning a guessed size."""
        in_cart = self._cart_item_ids(ctx)
        pending: list[dict[str, Any]] = []
        for item in args.get("cartItems") or []:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("menu_item_id") or "").strip()
            supplied = item.get("variantsV2") or item.get("variants") or item.get("variations")
            if supplied:
                # The planner already picked variants — but it can get the SHAPE wrong
                # (Sarvam swaps group name/id into the fields), which Swiggy 500s on.
                # Validate against the real menu and repair invalid rows to defaults.
                self._repair_variants(item, iid, ctx)
                continue
            # An item already in the cart keeps its chosen variant through the upsert
            # fold — a bare quantity change must NOT re-prompt for crust/size.
            if iid and iid in in_cart:
                continue
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

    def _repair_variants(self, item: dict[str, Any], iid: str, ctx: ExecCtx) -> None:
        """Validate the planner's variantsV2 against the REAL menu and repair ONLY
        provably-invalid rows. A correct selection is left untouched; a malformed one
        (e.g. the group NAME placed in the group_id field — Sarvam's mistake) is
        replaced with valid in-stock defaults, or dropped so Swiggy auto-picks — both
        prevent the 500 the bad shape triggers. No menu to check against → no-op."""
        supplied = item.get("variantsV2")
        if not isinstance(supplied, list) or not iid:
            return
        real_v2 = (self._variant_meta(iid, ctx) or {}).get("variantsV2") or []
        if not real_v2 or variant_rows_valid(supplied, real_v2):
            return  # nothing to validate against, or the rows are already valid
        picked = pick_default_variants(real_v2)
        if picked:
            item["variantsV2"] = picked
        else:
            item.pop("variantsV2", None)  # let Swiggy auto-pick its default

    def before_call(self, tool: str, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        # Guarantee the committed delivery address on every address-taking tool.
        addr_key = _ADDRESS_ARG.get(tool)
        if addr_key:
            self._ensure_address(args, addr_key, ctx)
        if tool != "update_food_cart" or not isinstance(args, dict):
            return None
        # Guarantee restaurantId — Swiggy REQUIRES it (live schema), but the planner
        # routinely omits it on a follow-up cart edit ("make it 2", "remove X"), which
        # fails the whole call. Backfill it (and the optional name) from the cart.
        self._ensure_restaurant(args, ctx)
        # Defer first when an item still needs a variant choice (before folding the
        # existing cart, which already carries its variants).
        deferred = self._variant_gate(args, ctx)
        if deferred is not None:
            return deferred
        self._reconcile_cart_items(args, ctx)
        # Swiggy errors an empty cartItems[] AND that failed call can poison the cart.
        # If reconcile produced nothing (e.g. every id was fabricated), skip the call
        # with a synthetic no-op instead of poking Swiggy.
        if not args.get("cartItems"):
            return {"is_error": False, "data": {"raw_text": "No cart changes to apply."}}
        return None

    def _ensure_restaurant(self, args: dict[str, Any], ctx: ExecCtx) -> None:
        """Backfill the required ``restaurantId`` (+ optional ``restaurantName``) on a
        cart edit the planner left it off. A concrete planner value (a real switch to
        another restaurant) is kept; otherwise we use the cart's own restaurant — the
        live snapshot read this turn, else the carried ``session_cart`` — falling back
        to the ``__RESTAURANT_ID__`` learned from a search this turn."""
        rid = str(args.get("restaurantId") or "").strip()
        session = ctx.session if isinstance(ctx.session, dict) else {}
        cart = session.get("session_cart") if isinstance(session.get("session_cart"), dict) else {}
        if not rid or rid.startswith("__"):
            placeholders = ctx.placeholders if isinstance(ctx.placeholders, dict) else {}
            ph = str(placeholders.get("__RESTAURANT_ID__") or "").strip()
            cart_res = str(cart.get("res_id") or "").strip()
            chosen = cart_res or (ph if ph and not ph.startswith("__") else "")
            if chosen:
                args["restaurantId"] = chosen
        if not str(args.get("restaurantName") or "").strip():
            rid_now = str(args.get("restaurantId") or "").strip()
            rname = str(cart.get("restaurant_name") or "").strip()
            if not rname and rid_now:
                for r in session.get("session_restaurants") or []:
                    if isinstance(r, dict) and str(r.get("res_id") or "").strip() == rid_now:
                        rname = str(r.get("name") or "").strip()
                        break
            if rname:
                args["restaurantName"] = rname

    def _reconcile_cart_items(self, args: dict[str, Any], ctx: ExecCtx) -> None:
        """Fold the planner's items onto the cart's existing lines (UPSERT) so a
        partial list never wipes the rest — Swiggy's update_food_cart is REPLACE-ALL.
        Folds the LIVE cart read in before_run; skips folding on a restaurant switch
        with new items (Swiggy replaces the cart per restaurant) or after a flush."""
        scratch = ctx.scratch if isinstance(ctx.scratch, dict) else {}
        existing = scratch.get("live_cart_rows")
        existing_rows = existing if isinstance(existing, list) else []
        session = ctx.session if isinstance(ctx.session, dict) else {}
        cart = session.get("session_cart")
        cart_res = str(cart.get("res_id") or "").strip() if isinstance(cart, dict) else ""
        target_res = str(args.get("restaurantId") or "").strip()
        switched_restaurant = bool(
            cart_res and target_res and not target_res.startswith("__") and cart_res != target_res
        )
        planner_has_items = bool(args.get("cartItems"))
        fold = not (
            scratch.get("_swiggy_food_cart_flushed") or (switched_restaurant and planner_has_items)
        )
        reconcile_cart_items(
            args,
            existing_rows=existing_rows if fold else [],
            valid_ids=self._valid_item_ids(ctx),
            fold=fold,
        )

    # ----------------------------------------------------------------- output
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
        # Persist the saved-address list resolved this turn so the next turn reuses it.
        rows = result.diagnostics.get("saved_address_rows")
        if isinstance(rows, list) and rows:
            merged["session_addresses"] = [
                {**r, "provider": self.id} for r in rows if isinstance(r, dict)
            ]
        # Seed the committed selection from the address resolved this turn ONLY when
        # none is set yet — never overwrite an explicit pick (which would silently
        # revert a user's switch). An explicit switch this turn WINS (mirrors Zepto).
        resolved = str(result.diagnostics.get("resolved_address_id") or "").strip()
        if resolved and not str(merged.get(_SESSION_SELECTED_ADDRESS) or "").strip():
            merged[_SESSION_SELECTED_ADDRESS] = resolved
        switched = str(result.diagnostics.get("switched_address_id") or "").strip()
        if switched:
            merged[_SESSION_SELECTED_ADDRESS] = switched
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
