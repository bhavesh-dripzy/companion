"""Swiggy Instamart — clean `McpProvider` (grocery; product search + LRU).

Supplies the Instamart-specific bits; the base drives the execute loop:

  * `stdio_spec` — mcp-remote launch for `mcp.swiggy.com/im` (tokens hydrated).
  * `build_attachments` — instamart_product_cards from real `ToolResult`s.
  * `merge` — fold this turn's products into the spinId LRU.
  * `plan` — delegate to the tuned legacy domain agent (parity bridge).

Scope: product *search* (read-only) is ported + validated against real Instamart
data. Cart cards stay on the executor path (get_cart snapshot enrichment); the
bootstrap swap is gated on those + session parity so dev never regresses.
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
from core.providers.mcp.info_cards import build_info_cards
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.placeholders import DEFAULT_ALIASES, DEFAULT_ISSUERS, PlaceholderResolver
from core.providers.mcp.remote import stdio_spec as remote_stdio_spec
from core.providers.mcp.result_cards import build_order_cards, build_tracking_card
from core.providers.plugins.swiggy_instamart.cards import (
    build_product_cards,
    normalize_address_rows,
)
from core.providers.plugins.swiggy_instamart.cart_cards import (
    build_cart_cards,
    build_spin_index,
    cart_snapshot,
)
from core.providers.plugins.swiggy_instamart.planner_context import build_context
from core.providers.plugins.swiggy_instamart.session import SwiggyInstamartSession, merge_products

_CART_TOOLS = {"get_cart", "update_cart", "clear_cart"}
_SEARCH_TOOLS = {"search_products", "your_go_to_items"}

# Tools that REQUIRE a delivery-address arg, and the (provider-specific) key each
# uses. The executor guarantees the resolved address is present on these so the
# planner can never break the call by omitting a required id.
_ADDRESS_ARG = {
    "search_products": "addressId",
    "your_go_to_items": "addressId",
    "update_cart": "selectedAddressId",
    "checkout": "addressId",
}

_DEFAULT_URL = "https://mcp.swiggy.com/im"
_SESSION_SELECTED_ADDRESS = "selected_instamart_address_id"

# Let the planner add a freshly-searched SKU in ONE plan: search_products issues
# __INSTAMART_SPIN__ (the top result's spinId) so update_cart can reference it
# without a second turn. Mirrors Zepto's __ZEPTO_PVID__ chain.
_INSTAMART_ALIASES = {**DEFAULT_ALIASES, "__INSTAMART_SPIN__": ("spinId", "spin_id")}
_INSTAMART_ISSUERS = {
    **DEFAULT_ISSUERS,
    "search_products": ("__INSTAMART_SPIN__",),
    "your_go_to_items": ("__INSTAMART_SPIN__",),
}


class _InstamartResolver(PlaceholderResolver):
    # The chained spinId is search-scoped — never persist it across turns.
    transient: frozenset[str] = frozenset({"__INSTAMART_SPIN__"})


class SwiggyInstamartProvider(McpProvider):
    resolver: PlaceholderResolver = _InstamartResolver(
        aliases=_INSTAMART_ALIASES, issuers=_INSTAMART_ISSUERS
    )
    id = "swiggy_instamart"
    label = "Swiggy Instamart MCP"
    auth_mode = AuthMode.OAUTH
    session_cls = SwiggyInstamartSession
    saved_addresses_tool = "get_addresses"
    saved_addresses_session_key = "session_instamart_addresses"

    def mcp_url(self, ctx: ExecCtx) -> str | None:
        return os.getenv("SWIGGY_INSTAMART_MCP_URL", _DEFAULT_URL)

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        return remote_stdio_spec(self.mcp_url(ctx), user_id=ctx.user_id)

    async def run(self, calls: list, ctx: ExecCtx) -> ProviderResult:
        result = await super().run(calls, ctx)
        # Surface the full saved-address list (from before_run) for the UI picker.
        rows = ctx.scratch.get("saved_address_rows")
        if isinstance(rows, list) and rows:
            result.diagnostics["saved_address_rows"] = rows
        # The address the search ACTUALLY ran against is authoritative for display:
        # post-substitution search args carry the concrete addressId (whether the
        # user switched via the picker or before_run resolved __ADDRESS_ID__).
        # Fall back to before_run's resolved id only when args omit it entirely.
        used = self._used_address_id(result.results)
        resolved = used or ctx.scratch.get("resolved_address_id")
        if resolved:
            result.diagnostics["resolved_address_id"] = resolved
        # Carry the products LRU so cart cards can enrich items (the cart payload
        # has no images / pack sizes — only spinId + name + price).
        session = ctx.session if isinstance(ctx.session, dict) else {}
        lru = session.get("session_instamart_products")
        if isinstance(lru, list) and lru:
            result.diagnostics["session_instamart_products"] = lru
        return result

    @staticmethod
    def _used_address_id(results: list) -> str | None:
        """The concrete addressId a search ran against this turn, if any."""
        for tr in results:
            if tr.tool in _SEARCH_TOOLS and tr.ok:
                aid = str((tr.arguments or {}).get("addressId") or "").strip()
                if aid and not aid.startswith("__"):
                    return aid
        return None

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        rows = result.diagnostics.get("saved_address_rows")
        addresses = normalize_address_rows(rows) if isinstance(rows, list) else []
        address_id = result.diagnostics.get("resolved_address_id") or (
            addresses[0]["id"] if addresses else None
        )
        session_products = result.diagnostics.get("session_instamart_products")
        return [
            *build_product_cards(result.results, address_id=address_id, addresses=addresses),
            *build_cart_cards(result.results, session_products=session_products),
            *build_order_cards(
                result.results, tools={"get_orders"}, provider_id=self.id, brand="instamart"
            ),
            *build_tracking_card(
                result.results, tools={"track_order"}, provider_id=self.id, brand="instamart"
            ),
            *build_info_cards(
                result.results,
                exclude={
                    "search_products",
                    "your_go_to_items",
                    "get_cart",
                    "update_cart",
                    "clear_cart",
                    "get_orders",
                    "track_order",
                    "get_addresses",
                    "report_error",
                },
                provider_id=self.id,
                brand="instamart",
            ),
        ]

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        merged = dict(slice_)
        # Persist the saved-address list resolved this turn (from before_run's
        # scratch, surfaced via diagnostics) so the next turn carries it without
        # another get_addresses round-trip.
        rows = result.diagnostics.get("saved_address_rows")
        if isinstance(rows, list) and rows:
            merged[self.saved_addresses_session_key] = [
                {**r, "provider": self.id} for r in rows if isinstance(r, dict)
            ]
        merge_saved_addresses(
            merged, result.results, provider=self.id, key=self.saved_addresses_session_key
        )
        products = merge_products(slice_.get("session_instamart_products"), result.results)
        if products:
            merged["session_instamart_products"] = products
        spin_index = build_spin_index(products, result.results)
        # Apply cart-affecting tools IN ORDER so the last one wins: a clear_cart
        # empties the snapshot (so a cleared cart never lingers as stale items),
        # while a later update_cart/get_cart in the same plan (e.g. clear→rebuild)
        # overwrites it with the fresh contents.
        for tr in result.results:
            if not tr.ok:
                continue
            if tr.tool == "clear_cart":
                merged["session_instamart_cart"] = {"is_empty": True, "items": []}
            elif tr.tool in _CART_TOOLS:
                snap = cart_snapshot(
                    tr.data if isinstance(tr.data, dict) else {}, spin_index=spin_index
                )
                if snap:
                    merged["session_instamart_cart"] = snap
        # SEED the committed selection from the cart's address ONLY when none is
        # set yet (first add, before any explicit pick). NEVER overwrite an
        # existing selection — the old code did, which silently reverted a user's
        # address switch back to the cart's current address every turn.
        cart = merged.get("session_instamart_cart")
        cart_addr = (
            str(cart.get("selected_address_id") or "").strip()
            if isinstance(cart, dict) and cart.get("items")
            else ""
        )
        if cart_addr and not str(merged.get(_SESSION_SELECTED_ADDRESS) or "").strip():
            merged[_SESSION_SELECTED_ADDRESS] = cart_addr
        return merged

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        # Pin the session's committed delivery address (cart address / explicit
        # selection) before the base resolves __ADDRESS_ID__ — otherwise the base
        # picks the nearest saved address each turn, which may not be serviceable.
        anchor = self._anchor_address(ctx)
        if anchor and "__ADDRESS_ID__" not in ctx.placeholders:
            ctx.placeholders["__ADDRESS_ID__"] = anchor
            ctx.scratch["resolved_address_id"] = anchor
        await super().before_run(session, ctx)

    @staticmethod
    def _anchor_address(ctx: ExecCtx) -> str | None:
        """The committed delivery address, authoritative for every Instamart call.

        The user's EXPLICIT selection (`selected_instamart_address_id` — set by
        the UI address picker, the saved default source-of-truth, or seeded from
        the cart on the first add) WINS. It serves two jobs:
          1. overrides the planner's per-call addressId, which drifts to the
             first saved address and mis-addresses searches/cart; and
          2. lets an explicit address SWITCH take effect even with a non-empty
             cart — preferring the cart's address here (the old design) silently
             pinned the user to the cart's store and ignored every switch.
        The live cart's address is only a fallback to seed the anchor before the
        user has made any explicit choice."""
        session = ctx.session if isinstance(ctx.session, dict) else {}
        selected = str(session.get(_SESSION_SELECTED_ADDRESS) or "").strip()
        if selected:
            return selected
        cart = session.get("session_instamart_cart")
        if isinstance(cart, dict) and cart.get("items"):
            return str(cart.get("selected_address_id") or "").strip() or None
        return None

    def before_call(self, tool: str, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        # Guarantee the resolved delivery address on every tool that requires it —
        # the planner sometimes omits the (required) addressId/selectedAddressId,
        # which Swiggy rejects with "delivery address is missing".
        addr_key = _ADDRESS_ARG.get(tool)
        if addr_key:
            self._ensure_address(args, addr_key, ctx)
        # update_cart is REPLACE-ALL on Swiggy's side, so a planner that lists
        # only the changed item would silently wipe the rest of the cart. Make it
        # an UPSERT in code: fold the planner's items onto the existing cart
        # (qty<=0 drops a line) so add / change-qty / remove all behave correctly
        # regardless of whether the planner re-listed every line.
        if tool == "update_cart":
            self._reconcile_cart_items(args, ctx)
        return None

    def _ensure_address(self, args: dict[str, Any], key: str, ctx: ExecCtx) -> None:
        """Set the address arg to the session's committed address.

        When an anchor (explicit selection / live cart address) exists it OVERRIDES
        whatever the planner put there — the planner's per-call addressId drifts to
        the first saved address, sending searches + cart calls to the wrong (often
        non-serviceable) store. With no anchor yet (first turn, no cart), fall back
        to filling only when the planner left it missing/blank."""
        anchor = self._anchor_address(ctx)
        if anchor:
            args[key] = anchor
            return
        val = str(args.get(key) or "").strip()
        if val and not val.startswith("__"):
            return
        resolved = self._resolved_address(ctx)
        if resolved:
            args[key] = resolved

    @staticmethod
    def _resolved_address(ctx: ExecCtx) -> str | None:
        placeholders = ctx.placeholders if isinstance(ctx.placeholders, dict) else {}
        for key in ("__ADDRESS_ID__", "swiggy_instamart:__ADDRESS_ID__"):
            val = str(placeholders.get(key) or "").strip()
            if val and not val.startswith("__"):
                return val
        scratch = ctx.scratch if isinstance(ctx.scratch, dict) else {}
        val = str(scratch.get("resolved_address_id") or "").strip()
        if val:
            return val
        session = ctx.session if isinstance(ctx.session, dict) else {}
        return str(session.get(_SESSION_SELECTED_ADDRESS) or "").strip() or None

    @staticmethod
    def _valid_spins(ctx: ExecCtx) -> set[str]:
        """Spin ids known-valid for the CURRENT store: this turn's search results
        (scratch) plus the carried products LRU plus items already in the cart.

        Swiggy rejects update_cart with a spinId that isn't a real product at the
        selected address ("No valid items in cart") — and the planner sometimes
        invents one (e.g. the price "239"). We only send ids drawn from a real
        search, so a fabricated id is dropped before it reaches Swiggy."""
        valid: set[str] = set(ctx.scratch.get("_instamart_valid_spins") or set())
        session = ctx.session if isinstance(ctx.session, dict) else {}
        for prod in session.get("session_instamart_products") or []:
            for var in (prod.get("variants") or []) if isinstance(prod, dict) else []:
                sid = str((var or {}).get("spin_id") or (var or {}).get("spinId") or "").strip()
                if sid:
                    valid.add(sid)
        cart = session.get("session_instamart_cart")
        for it in (cart.get("items") if isinstance(cart, dict) else None) or []:
            sid = str((it or {}).get("spin_id") or (it or {}).get("spinId") or "").strip()
            if sid:
                valid.add(sid)
        return valid

    def harvest(self, tool: str, envelope: dict[str, Any], ctx: ExecCtx) -> None:
        # Record spinIds returned by a search so a later update_cart in the same
        # plan can be validated against real, store-correct product ids.
        if tool in _SEARCH_TOOLS and isinstance(envelope, dict) and not envelope.get("is_error"):
            data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
            valid = ctx.scratch.setdefault("_instamart_valid_spins", set())
            for prod in data.get("products") or []:
                for var in (prod.get("variations") or []) if isinstance(prod, dict) else []:
                    sid = str((var or {}).get("spinId") or "").strip()
                    if sid:
                        valid.add(sid)
        # A successful clear_cart earlier in THIS plan means the existing cart is
        # gone — a later update_cart must rebuild from scratch, not merge onto it.
        if tool == "clear_cart" and not envelope.get("is_error"):
            ctx.scratch["_instamart_cart_cleared"] = True

    def _reconcile_cart_items(self, args: dict[str, Any], ctx: ExecCtx) -> None:
        merged: dict[str, int] = {}
        session = ctx.session if isinstance(ctx.session, dict) else {}
        cart = session.get("session_instamart_cart") if isinstance(session, dict) else None
        # Don't fold the existing cart in two cases: (1) a clear_cart ran earlier
        # this plan, or (2) this update_cart targets a DIFFERENT address than the
        # cart was built at (an address switch) — those items live on the old
        # store and would contaminate / be rejected at the new one. Start fresh.
        cart_addr = (
            str(cart.get("selected_address_id") or "").strip() if isinstance(cart, dict) else ""
        )
        target_addr = str(args.get("selectedAddressId") or "").strip()
        switched = bool(
            cart_addr
            and target_addr
            and not target_addr.startswith("__")
            and cart_addr != target_addr
        )
        if not ctx.scratch.get("_instamart_cart_cleared") and not switched:
            existing = cart.get("items") if isinstance(cart, dict) else None
            for it in existing or []:
                if not isinstance(it, dict):
                    continue
                sid = str(it.get("spin_id") or it.get("spinId") or "").strip()
                if not sid:
                    continue
                try:
                    qty = int(it.get("quantity") or 1)
                except (TypeError, ValueError):
                    qty = 1
                if qty > 0:
                    merged[sid] = qty
        valid = self._valid_spins(ctx)
        for it in args.get("items") or []:
            if not isinstance(it, dict):
                continue
            sid = str(it.get("spinId") or it.get("spin_id") or "").strip()
            if not sid:
                continue
            try:
                qty = int(it.get("quantity"))
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                # Removal is by OMISSION — never send qty=0 (Swiggy errors on it
                # AND corrupts the cart session). Drop the line from the payload.
                merged.pop(sid, None)
                continue
            # Reject a spinId the planner invented (not from a real search at this
            # store). Skipping it is far better than Swiggy nuking the whole call
            # with "No valid items in cart".
            if valid and sid not in valid:
                # Drop a spinId the planner invented (not from a real search at
                # this store) — skipping it beats Swiggy rejecting the whole call
                # with "No valid items in cart".
                continue
            merged[sid] = qty
        args["items"] = [{"spinId": sid, "quantity": qty} for sid, qty in merged.items()]

    def planner_context(self, ctx: Any) -> str:
        return build_context(ctx)
