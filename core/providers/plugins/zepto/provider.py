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
from core.providers.mcp.result_parse import tool_result_payload
from core.providers.plugins.zepto.address import (
    extract_zepto_address_rows,
    normalize_address_rows,
)
from core.providers.plugins.zepto.cards import build_product_cards, products_from_search
from core.providers.plugins.zepto.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.zepto.planner_context import build_context
from core.providers.plugins.zepto.session import ZeptoSession, merge_products

_CART_TOOLS = {"view_cart", "update_cart"}
_SEARCH_TOOLS = {"search_products", "search_multiple_products"}

# Zepto can error a view_cart on an EMPTY cart with one of these — that's not a
# failure, the cart is just empty. Reclassify it to a successful empty cart so the
# narrator reports "your cart is empty", never "technical issue" (mirrors the
# Instamart get_cart empty-cart handling).
_EMPTY_CART_MARKERS = (
    "cart not found",
    "session expired",
    "cart is empty",
    "no items",
    "empty cart",
)


def _is_empty_cart_error(detail: str | None) -> bool:
    text = (detail or "").lower()
    return any(m in text for m in _EMPTY_CART_MARKERS)


_DEFAULT_URL = "https://mcp.zepto.co.in/mcp"
# The committed Zepto delivery ADDRESS id — pinned by companion_api at the
# request boundary (source of truth, mirrors Instamart). select_saved_address
# binds BOTH the store/serviceability AND the order's delivery doorstep to it.
_SESSION_ZEPTO_ADDRESS = "selected_zepto_address_id"
# Legacy GPS point {latitude, longitude, ...} — kept as an optional fallback for
# users who granted location but haven't picked a saved address yet.
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
    capability_class = "grocery"
    cart_session_key = "session_zepto_cart"
    # Zepto's list_saved_addresses returns prose, not the structured shape the
    # base's saved_addresses_tool path expects — so we leave that None and do our
    # own fetch + parse in before_run (extract_zepto_address_rows).
    saved_addresses_session_key = "session_zepto_addresses"

    def mcp_url(self, ctx: ExecCtx) -> str | None:
        return os.getenv("ZEPTO_MCP_URL", _DEFAULT_URL)

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        return remote_stdio_spec(self.mcp_url(ctx), user_id=ctx.user_id)

    @staticmethod
    def _anchor_address(ctx: ExecCtx) -> str | None:
        """The committed Zepto delivery address id, authoritative this session.

        The user's EXPLICIT selection (`selected_zepto_address_id` — set by the
        UI picker or the saved default source-of-truth) wins. Binding it via
        select_saved_address pins BOTH the store and the order doorstep, and
        lets an address SWITCH take effect mid-session (mirrors Instamart)."""
        session = ctx.session if isinstance(ctx.session, dict) else {}
        return str(session.get(_SESSION_ZEPTO_ADDRESS) or "").strip() or None

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
        """Guard: a saved address (preferred) OR a GPS point is required to bind a
        store — without either, return needs_location without opening a session."""
        if self._anchor_address(ctx) is None and self._effective_geo(ctx) is None:
            exc = NeedsLocationError("Pick a Zepto delivery address (or share your location).")
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
        result = await super().run(calls, ctx)
        # An empty Zepto cart can make view_cart ERROR ("cart is empty" / "cart not
        # found") — that's not a failure. Reclassify it to a successful empty cart
        # so the narrator (and the failure fallback) report "your cart is empty",
        # never "technical issue", and merge clears any stale snapshot.
        for tr in result.results:
            if tr.tool == "view_cart" and not tr.ok and _is_empty_cart_error(tr.error_detail):
                tr.ok = True
                tr.error = None
                tr.error_detail = None
                tr.data = {"is_error": False, "data": {"items": [], "isEmpty": True}}
        # Surface the saved-address list + resolved address (from before_run) for
        # the UI picker and the "Delivering to" bar (mirrors Instamart).
        rows = ctx.scratch.get("saved_address_rows")
        if isinstance(rows, list) and rows:
            result.diagnostics["saved_address_rows"] = rows
        resolved = ctx.scratch.get("resolved_zepto_address_id")
        if resolved:
            result.diagnostics["resolved_zepto_address_id"] = resolved
        # An explicit address switch this turn (planner-resolved select_saved_address
        # to a different saved id) must re-commit the session's selection so it
        # sticks next turn + persists as the DB default (mirrors Instamart).
        switched = ctx.scratch.get("zepto_switched_address_id")
        if switched:
            result.diagnostics["switched_address_id"] = switched
        # A committed address deleted/renamed on the Zepto app (see before_run) — let
        # merge clear it from the session and core_shadow clear the DB default.
        stale = ctx.scratch.get("zepto_stale_committed_address")
        if stale:
            result.diagnostics["stale_committed_address"] = stale
        return result

    @staticmethod
    def _row_has_id(rows: list[dict[str, Any]], address_id: str) -> bool:
        """True when ``address_id`` appears in a saved-address row list (keyed by
        ``address_id`` or ``id``)."""
        target = (address_id or "").strip()
        return bool(target) and any(
            isinstance(r, dict) and str(r.get("address_id") or r.get("id") or "").strip() == target
            for r in rows
        )

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        await super().before_run(session, ctx)
        # Stable per-user device ID required by update_cart.
        if "__ZEPTO_DEVICE_ID__" not in ctx.placeholders:
            ctx.placeholders["__ZEPTO_DEVICE_ID__"] = str(
                uuid.uuid5(uuid.NAMESPACE_DNS, f"zepto-device-{ctx.user_id}")
            )
        # Fetch + parse the saved-address list (prose) once per session so the UI
        # picker / "Delivering to" bar can resolve the committed address's label.
        carried = (
            ctx.session.get("session_zepto_addresses") if isinstance(ctx.session, dict) else None
        )
        rows = carried if isinstance(carried, list) and carried else None
        if rows is None:
            try:
                raw = await call_tool(session, "list_saved_addresses", {})
                rows = extract_zepto_address_rows(tool_result_payload(raw))
            except Exception:
                rows = []
        if rows:
            ctx.scratch["saved_address_rows"] = rows

        # PRIMARY: bind the store + delivery doorstep from the committed saved
        # address (select_saved_address pins both, mirrors Instamart). FALLBACK:
        # the legacy GPS point for users who haven't picked an address yet.
        anchor = self._anchor_address(ctx)
        # TRUST ZEPTO'S SAVED-ADDRESS LIST (the "address changed outside chat" case):
        # Zepto has no per-cart address to read back (it rebinds per turn from the
        # committed id), so the live saved-address list is the source of truth. A
        # committed id absent from it was DELETED/RENAMED on the Zepto app — binding
        # it pins a dead id (stale "Delivering to" bar, a store that no longer maps
        # to it, and it gets re-committed forever). Reconcile: drop it, fall back to
        # GPS, and flag it so merge clears the committed id (Zepto's analogue of
        # Instamart's self-heal of a drifted committed address). Only act on a
        # NON-EMPTY list — an empty fetch is inconclusive, never a deletion signal.
        if anchor and rows and not self._row_has_id(rows, anchor):
            ctx.scratch["zepto_stale_committed_address"] = anchor
            anchor = None
        if anchor:
            ctx.placeholders["__ZEPTO_ADDRESS_ID__"] = anchor
            ctx.scratch["resolved_zepto_address_id"] = anchor
            try:
                await call_tool(session, "select_saved_address", {"addressId": anchor})
                ctx.scratch["zepto_store_registered"] = True
            except Exception:
                pass  # planner-emitted select_saved_address is the fallback
        geo = self._effective_geo(ctx)
        if geo is not None:
            lat, lng = geo
            ctx.placeholders["__USER_LAT__"] = lat
            ctx.placeholders["__USER_LNG__"] = lng
            if not ctx.scratch.get("zepto_store_registered"):
                try:
                    await call_tool(
                        session, "get_location_serviceability", {"latitude": lat, "longitude": lng}
                    )
                    ctx.scratch["zepto_store_registered"] = True
                except Exception:
                    pass  # planner-emitted serviceability call is the fallback

    def normalize_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # Zepto's pageNumber is 0-indexed; planners routinely emit pageNumber:1
        # thinking it means "first page". Drop it so the server uses the default (0).
        if tool in {"search_products", "search_multiple_products"} and args.get("pageNumber") == 1:
            return {k: v for k, v in args.items() if k != "pageNumber"}
        return args

    def before_call(self, tool: str, args: dict[str, Any], ctx: ExecCtx) -> dict[str, Any] | None:
        # Short-circuit the planner's store-binding calls ONLY when they're
        # redundant with what before_run already bound. A select_saved_address to
        # a DIFFERENT id is a real address switch — let it through.
        if tool == "get_location_serviceability" and ctx.scratch.get("zepto_store_registered"):
            return {
                "is_error": False,
                "data": {"raw_text": "Store already registered for this session."},
            }
        if tool == "select_saved_address":
            target = str(args.get("addressId") or "").strip()
            resolved = str(ctx.scratch.get("resolved_zepto_address_id") or "").strip()
            # Redundant re-bind of the already-selected address — skip the round-trip.
            if ctx.scratch.get("zepto_store_registered") and (not target or target == resolved):
                return {
                    "is_error": False,
                    "data": {"raw_text": "Address already selected for this session."},
                }
            # A switch to a DIFFERENT, known-saved address: let it bind AND record it
            # so run() surfaces it and merge re-commits the session's delivery address
            # (otherwise the switch binds the store this turn but reverts next turn).
            # An unknown id is a planner fabrication — never treat it as a switch.
            if target and target != resolved and self._is_saved_address(target, ctx):
                ctx.scratch["zepto_switched_address_id"] = target
                ctx.scratch["resolved_zepto_address_id"] = target
            return None
        if tool == "update_cart":
            self._reconcile_cart_items(args, ctx)
            # Nothing valid left to send (e.g. every item was a fabricated pvid). An
            # empty cartItems would error/no-op on Zepto — skip with a synthetic
            # no-op instead of poking the server (mirrors Instamart).
            if not args.get("cartItems"):
                return {"is_error": False, "data": {"raw_text": "No cart changes to apply."}}
        return None

    @staticmethod
    def _is_saved_address(address_id: str, ctx: ExecCtx) -> bool:
        """True when ``address_id`` is one of the user's saved Zepto addresses.

        Guards the switch path: the planner only emits a concrete addressId by
        copying it from the saved-addresses block, so a value that isn't a known
        saved id is a fabrication and must not re-commit the delivery address."""
        candidates: list[Any] = []
        rows = ctx.scratch.get("saved_address_rows") if isinstance(ctx.scratch, dict) else None
        if isinstance(rows, list):
            candidates.extend(rows)
        session = ctx.session if isinstance(ctx.session, dict) else {}
        carried = session.get("session_zepto_addresses")
        if isinstance(carried, list):
            candidates.extend(carried)
        return ZeptoProvider._row_has_id(candidates, address_id)

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
        # Always replace with the reconciled list — when every item was dropped
        # (all fabricated), send [] so before_call's short-circuit catches it
        # rather than forwarding the planner's bad payload to Zepto.
        args["cartItems"] = out

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        rows = result.diagnostics.get("saved_address_rows")
        addresses = normalize_address_rows(rows) if isinstance(rows, list) else []
        address_id = result.diagnostics.get("resolved_zepto_address_id") or (
            addresses[0]["id"] if addresses else None
        )
        return [
            *build_product_cards(result.results, address_id=address_id, addresses=addresses),
            *build_cart_cards(result.results, address_id=address_id, addresses=addresses),
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
        # Persist the saved-address list resolved this turn so the next turn reuses
        # it without another list_saved_addresses round-trip (mirrors Instamart).
        rows = result.diagnostics.get("saved_address_rows")
        if isinstance(rows, list) and rows:
            merged["session_zepto_addresses"] = [
                {**r, "provider": self.id} for r in rows if isinstance(r, dict)
            ]
        # Seed the committed selection from the address resolved this turn ONLY
        # when none is set yet — never overwrite an explicit pick (which would
        # silently revert a user's address switch).
        resolved = str(result.diagnostics.get("resolved_zepto_address_id") or "").strip()
        if resolved and not str(merged.get(_SESSION_ZEPTO_ADDRESS) or "").strip():
            merged[_SESSION_ZEPTO_ADDRESS] = resolved
        # An explicit switch this turn WINS over the committed selection — the user
        # asked to deliver elsewhere; it becomes the session's source of truth and
        # (via core_shadow) the DB default (mirrors Instamart's switched_address_id).
        switched = str(result.diagnostics.get("switched_address_id") or "").strip()
        if switched:
            merged[_SESSION_ZEPTO_ADDRESS] = switched
        # A committed address deleted/renamed on the Zepto app was dropped this turn
        # — clear it so we stop re-committing a dead id (next turn re-resolves from
        # the saved default / GPS). An explicit switch this turn still wins.
        stale = str(result.diagnostics.get("stale_committed_address") or "").strip()
        if (
            stale
            and not switched
            and str(merged.get(_SESSION_ZEPTO_ADDRESS) or "").strip() == stale
        ):
            merged.pop(_SESSION_ZEPTO_ADDRESS, None)
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
