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
from core.agent.domains import grocery_search as S
from core.agent.domains import grocery_view as V
from core.agent.events import Emit, noop, say
from core.agent.registry import REGISTRY

DOMAIN = "grocery"

# Fixed per-action openers (no item involved). add/remove/search fold in the item separately below.
_OPENER = {
    "set_qty": "Theek hai, quantity update karta hoon…",
    "clear": "Theek hai, cart khaali kar raha hoon…",
    "show_cart": "Aapka cart dikhata hoon…",
    "checkout": "Theek hai, checkout shuru karta hoon…",
    "switch_context": "Address change kar raha hoon…",
}


def _opener(action: str, intent: dict[str, Any]) -> str | None:
    """An instant 'I'm on it' line, emitted the moment an action starts (before the slow MCP work),
    so the assistant acknowledges in real time. add/remove fold in the item the user named; search
    has its own item-aware opener in fan_out (so it returns None here)."""
    named = (
        ""
        if intent.get("from_tap")
        else ", ".join(r.get("phrase", "") for r in intent.get("item_refs", []) if r.get("phrase"))
    )
    if action == "add":
        return (
            f"Theek hai, {named} cart mein add karta hoon…"
            if named
            else "Theek hai, add karta hoon…"
        )
    if action == "remove":
        return f"Ok, {named} cart se hata raha hoon…" if named else "Ok, cart se hata raha hoon…"
    return _OPENER.get(action)


class GroceryDomainHandler:
    async def handle(self, state, intent: dict[str, Any], emit: Emit = noop) -> dict[str, Any]:
        action = intent["action"]
        providers = REGISTRY.providers_for(DOMAIN)

        # ---- provider selection (§6b: hint > fan-out > sticky > inferred item > ask) ----
        chosen, ask = await R.choose_providers(state, intent, providers)
        if ask is not None:
            return ask
        log.debug(
            "GROCERY action=%s → providers=%s (active=%s)", action, chosen, state.active_provider
        )
        # Acknowledge instantly (streams as the first typed line); search self-narrates in fan_out.
        if opener := _opener(action, intent):
            await emit(say(opener))

        if (
            action == "search"
        ):  # concurrent fan-out; narrates + streams each app's cards as it finishes
            return await S.fan_out(state, intent, chosen, emit)
        if action == "show_cart":
            return await self._show_cart(state, chosen[0])
        if action == "checkout":
            return await self._checkout(state, chosen[0])
        if action == "clear":
            return await self._clear(state, chosen[0])
        if action == "switch_context":
            return await A.switch_context(state, intent, chosen[0])
        if action in ("add", "set_qty", "remove"):
            return await self._cart_op(state, intent, chosen[0], emit)
        return {
            "domain": DOMAIN,
            "status": "ok",
            "cards": [],
            "error": None,
            "note": f"'{action}' abhi grocery mein available nahi hai.",
        }

    # ---------------- add / set_qty / remove ----------------
    async def _cart_op(self, state, intent, pid: str, emit: Emit = noop) -> dict[str, Any]:
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

            if intent.get(
                "from_tap"
            ):  # a UI tap carries the exact item identity → no LLM resolution
                refs = {
                    "resolved": [
                        {
                            "ref": r.get("phrase"),
                            "qty": int(r.get("qty", 1)),
                            "item_id": r.get("item_id"),
                            "secondary_id": r.get("secondary_id"),
                            "name": r.get("name"),
                        }
                        for r in requests
                        if r.get("phrase") or r.get("item_id")
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
            # The quantity is the user's explicit number (from the router) — NEVER the resolver LLM's
            # guess. The resolver only picks WHICH item; forcing the qty here stops "set to 2" silently
            # resolving to 1 (the LLM gets confused by sizes like "2 Pieces" in the name). Single-item
            # op only (the norm for set_qty/remove/add); multi-item keeps each request's own qty.
            if len(requests) == 1 and resolved:
                for r in resolved:
                    r["qty"] = int(requests[0].get("qty", 1))
            result = None
            if resolved:  # write the items that DID resolve — even if others are ambiguous/unknown
                delta, meta = V.build_delta(state, resolved)
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
            res = await S.fan_out(state, sub, [pid], emit)
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
