"""GroceryDomainHandler — products → cart, across multiple providers (Instamart, Zepto).

Owns: provider selection (§6b), fan-out search, LLM resolution (+ validation gate + re-search
on miss), and the per-provider CartStrategy. Returns a structured result (cards + error +
context_header) that the Narrator/endpoint render. Adding a grocery provider needs no changes here.
Stateless rendering/resolution helpers live in grocery_view.py (file-size budget).
"""

from __future__ import annotations

import contextlib
from typing import Any

from core.agent.cart import strategy_for
from core.agent.context import CONTEXT
from core.agent.contracts import Cart, fmt_minor
from core.agent.debug import log, short
from core.agent.domains import grocery_address as A
from core.agent.domains import grocery_resolve as R
from core.agent.domains import grocery_view as V
from core.agent.legacy_cards import products_attachment
from core.agent.registry import REGISTRY
from core.agent.state import ConversationState

DOMAIN = "grocery"


class GroceryDomainHandler:
    async def handle(self, state: ConversationState, intent: dict[str, Any]) -> dict[str, Any]:
        action = intent["action"]
        providers = REGISTRY.providers_for(DOMAIN)

        # ---- provider selection (§6b: hint > fan-out > sticky > inferred item > ask) ----
        chosen, ask = await R.choose_providers(state, intent, providers)
        if ask is not None:
            return ask
        log.debug(
            "GROCERY action=%s → providers=%s (active=%s)", action, chosen, state.active_provider
        )

        if action == "search":
            return await self._search(state, intent, chosen)
        if action == "show_cart":
            return await self._show_cart(state, chosen[0])
        if action == "checkout":
            return await self._checkout(state, chosen[0])
        if action == "clear":
            return await self._clear(state, chosen[0])
        if action == "switch_context":
            return await A.switch_context(state, intent, chosen[0])
        if action in ("add", "set_qty", "remove"):
            return await self._cart_op(state, intent, chosen[0])
        return {
            "domain": DOMAIN,
            "status": "ok",
            "cards": [],
            "error": None,
            "note": f"'{action}' abhi grocery mein available nahi hai.",
        }

    # ---------------- search (fan-out) ----------------
    async def _search(self, state, intent, chosen: list[str]) -> dict[str, Any]:
        phrases = [r.get("phrase", "") for r in intent.get("item_refs", [])] or [
            intent.get("raw_query", "")
        ]
        flt = V.filter_from_intent(intent)  # price ceiling / sort, if the user asked
        cards: list[dict[str, Any]] = []
        attachments: list[dict[str, Any]] = []
        for pid in chosen:
            adapter = REGISTRY.provider(pid)
            try:
                async with adapter.open_session(state.user_id) as s:
                    ctx = await CONTEXT.exec_ctx(adapter, s, state)
                    await adapter.ensure_context(s, ctx)
                    for q in [p for p in phrases if p]:
                        products = await adapter.search(s, q, ctx)
                        # Store ONLY what we render (Instamart: 8 products' variants, Zepto: 12).
                        # The user can only add what they saw, so this is the right candidate set.
                        shown = V.shown_for(pid, V.apply_filter(products, flt))
                        state.catalog.append(shown)
                        log.debug(
                            "SEARCH %s q=%r → %d raw, %d shown/stored %s (top=%s)",
                            pid,
                            q,
                            len(products),
                            len(shown),
                            flt or "-",
                            short([p.name for p in shown[:3]]),
                        )
                        cards.append(V.product_card(pid, q, shown, flt))
                        att = products_attachment(pid, q, shown)
                        if att:
                            attachments.append(att)
            except Exception as e:
                cards.append(
                    {"type": "error", "provider": pid, "message": f"{type(e).__name__}: {e}"}
                )
        return {
            "domain": DOMAIN,
            "status": "ok",
            "cards": cards,
            "attachments": attachments,
            "error": None,
            "context_header": V.header(state),
            **V.addr_keys(state, chosen),
        }

    # ---------------- add / set_qty / remove ----------------
    async def _cart_op(self, state, intent, pid: str) -> dict[str, Any]:
        action = intent["action"]
        adapter = REGISTRY.provider(pid)
        strat = strategy_for(adapter.cart_strategy)
        requests = intent.get("item_refs", [])
        async with adapter.open_session(state.user_id) as s:
            ctx = await CONTEXT.exec_ctx(adapter, s, state)
            await adapter.ensure_context(s, ctx)
            # Server is the source of truth: refresh the cache before resolving/writing so a stale
            # or restarted memory cart can never clobber the real cart (replace-all safety).
            V.refresh_cart(state, pid, await adapter.read_cart(s, ctx))

            # set_qty/remove on an empty cart → clean "cart is empty" (after the live read)
            if action in ("set_qty", "remove") and not state.cart(pid).items:
                return V.cart_result(
                    state, pid, state.cart(pid), status="error", error="EMPTY_CART", message=""
                )

            if intent.get("from_tap"):  # a UI tap carries the exact ref → no LLM resolution
                refs = {
                    "resolved": [
                        {"ref": r["phrase"], "qty": int(r.get("qty", 1))}
                        for r in requests
                        if r.get("phrase")
                    ],
                    "ambiguous": [],
                    "unresolved": [],
                }
            else:
                # set_qty/remove resolve against the IN-CART items only → reliable for "doodh"/"coke"
                refs = await R.resolve_refs(
                    state, adapter, s, ctx, pid, requests, cart_only=action in ("set_qty", "remove")
                )
            resolved = refs.get("resolved", [])
            result = None
            if resolved:  # write the items that DID resolve — even if others are ambiguous/unknown
                delta: dict[str, int] = {}
                meta: dict[str, dict] = {}
                for r in resolved:
                    prod = state.catalog.by_ref(r.get("ref"))
                    if not prod:
                        continue
                    delta[prod.item_id] = int(r.get("qty", 1))
                    meta[prod.item_id] = {
                        "name": prod.name,
                        "secondary_id": prod.secondary_id,
                        "price_minor": prod.price_minor,
                    }
                log.debug("CART %s strategy=%s delta=%s", pid, adapter.cart_strategy, short(delta))
                result = await strat.set_items(adapter, s, state.cart(pid), delta, meta, ctx)
                log.debug(
                    "CART %s → ok=%s error=%s items=%d total=%s",
                    pid,
                    result.ok,
                    result.error.value if result.error else None,
                    len(result.cart.items) if result.cart else 0,
                    fmt_minor(result.cart.total_minor) if result.cart else "-",
                )
                if result.ok and result.cart is not None:
                    await adapter.price_cart(s, ctx, result.cart)

        # ---- commit the write, then build the response (outside the session) ----
        state.active_provider = pid
        if result is not None:
            if result.ok and result.cart is not None:
                state.carts[pid] = V.enrich(result.cart, state)
            else:  # the resolved write failed (cart repaired by the strategy) → surface the error
                return V.cart_result(
                    state,
                    pid,
                    state.cart(pid),
                    status="error",
                    error=result.error.value,
                    message=result.message,
                    detail=result.detail,
                    **V.addr_keys(state, [pid]),
                )
        # ---- route the leftovers (resolved items are already in the cart) ----
        added = [p.name for r in resolved if (p := state.catalog.by_ref(r.get("ref")))]
        ambiguous, unresolved = refs.get("ambiguous", []), refs.get("unresolved", [])
        # FIND: an ADD for something NOT in context → discover it via the normal search path (so the
        # user picks from a real product deck with images/sizes), not a fabricated ambiguity picker.
        if action == "add" and unresolved:
            sub = {**intent, "item_refs": [{"phrase": p, "qty": 1} for p in unresolved]}
            res = await self._search(state, sub, [pid])
            res["note"] = V.find_note(added, unresolved)  # "X add kiya. Y ye rahe — kaunsa?"
            return res
        # ASK: a genuine tie among ALREADY-SHOWN items → the disambiguation picker (partial-ambiguity).
        if ambiguous:
            return V.disambiguation_result(
                state, pid, added, R.disambig_options(state, pid, refs), []
            )
        # set_qty/remove targeting something not in the cart → clean "not in cart" (no fake success).
        if unresolved or not resolved:
            miss = (
                f"'{', '.join(unresolved)}' aapke cart mein nahi mila."
                if unresolved
                else "Woh item nahi mila."
            )
            return V.not_found(state, pid, miss)
        return V.cart_result(state, pid, state.cart(pid), **V.addr_keys(state, [pid]))

    # ---------------- show cart / checkout / clear ----------------
    async def _show_cart(self, state, pid: str) -> dict[str, Any]:
        await V.read_into_cache(state, pid)  # server is source of truth
        return V.cart_result(state, pid, state.cart(pid), **V.addr_keys(state, [pid]))

    async def _checkout(self, state, pid: str) -> dict[str, Any]:
        await V.read_into_cache(state, pid)
        cart = state.cart(pid)
        if cart.is_empty:
            return V.cart_result(state, pid, cart, status="error", error="EMPTY_CART", message="")
        # NOT implemented (real money) — return a clear note so the narrator never invents success.
        return V.cart_result(
            state,
            pid,
            cart,
            status="checkout_unavailable",
            note="Checkout abhi enable nahi hai — order place NAHI hua. Coming soon.",
        )

    async def _clear(self, state, pid: str) -> dict[str, Any]:
        adapter = REGISTRY.provider(pid)
        cart = Cart(provider=pid)
        with contextlib.suppress(Exception):
            async with adapter.open_session(state.user_id) as s:
                ctx = await CONTEXT.exec_ctx(adapter, s, state)
                await adapter.ensure_context(s, ctx)
                await adapter.clear(s, ctx)
                res = await adapter.read_cart(s, ctx)  # confirm the REAL state, don't fake empty
                if res.ok and res.cart is not None:
                    cart = V.enrich(res.cart, state)
        state.carts[pid] = cart
        note = (
            "Cart khaali kar diya."
            if cart.is_empty
            else "Cart poori clear nahi ho payi — kuch items reh gaye."
        )
        return V.cart_result(state, pid, cart, note=note)


HANDLER = GroceryDomainHandler()
