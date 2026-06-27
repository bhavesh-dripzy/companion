"""Grounded grocery domain — the 3-LLM flow for a TEXT turn:

  1) router (upstream)      picks the domain
  2) grocery_brain          ONE call → {actions, ack}; actions are the ONLY thing code runs
  3) code                   executes the actions against MCP (search/add/set_qty/remove/clear/show/
                            checkout), owning every number, and records FACTS (`outcomes`)
  4) grocery_response       narrates from the real `outcomes` (+ message/history) → the final reply

The brain decides, code does, the responder reports reality — so the reply can never claim something
that didn't happen. The MCP/cart machinery is the classic one (search via the adapter, writes via the
CartStrategy), untouched.
"""

from __future__ import annotations

from typing import Any

from core.agent.cart import strategy_for
from core.agent.context import CONTEXT
from core.agent.debug import log, short
from core.agent.domains import grocery_search as S
from core.agent.domains import grocery_view as V
from core.agent.domains.grocery_ground import (
    _cart_compact,
    _cart_facts,
    _found,
    _money,
    _old_items,
    _resolve_selector,
)
from core.agent.errors import user_message
from core.agent.events import Emit, noop, say
from core.agent.llm import grocery_brain, grocery_response
from core.agent.registry import REGISTRY

DOMAIN = "grocery"
_NO_APP = (
    "Abhi koi grocery app connected nahi hai. Instamart ya Zepto connect karke dobara try karo."
)


def _connected(user_id: str) -> list[str]:
    """Apps this user has connected — the gate is non-interactive (cached), never auth-prompts."""
    return [p.id for p in REGISTRY.providers_for(DOMAIN) if p.is_connected(user_id)]


async def _apply_cart(state, pid: str, ops: list[dict[str, Any]]):
    """Run a batch of cart ops for ONE provider via its CartStrategy. Each op = {ref, qty, mode}
    where mode ∈ {add (ADDITIVE), set (absolute), remove}. The live server cart is read first
    (replace-all safety), so an additive add computes target = current + qty correctly.

    Returns (attachments, ok, error): `ok` is whether the write actually landed — a non-serviceable /
    closed store (Instamart STORE_CLOSED) returns ok=False so the caller never claims a false success."""
    adapter = REGISTRY.provider(pid)
    strat = strategy_for(adapter.cart_strategy)
    ok, error = False, None
    async with adapter.open_session(state.user_id) as s:
        ctx = await CONTEXT.exec_ctx(adapter, s, state)
        await adapter.ensure_context(s, ctx)
        V.refresh_cart(state, pid, await adapter.read_cart(s, ctx))  # server is source of truth
        cart = state.cart(pid)
        resolved: list[dict[str, Any]] = []
        for o in ops:
            prod = state.catalog.by_ref(o["ref"])
            if prod is None:
                continue
            if o["mode"] == "remove":
                target = 0
            elif o["mode"] == "add":
                target = cart.qty_of(prod.item_id) + int(o.get("qty", 1))
            else:  # set (absolute)
                target = int(o.get("qty", 1))
            resolved.append({"ref": o["ref"], "qty": target})
        delta, meta = V.build_delta(state, resolved)
        log.debug("AGENT CART %s delta=%s", pid, short(delta))
        result = await strat.set_items(adapter, s, cart, delta, meta, ctx)
        ok, error = result.ok, result.error
        if ok and result.cart is not None:
            await adapter.price_cart(s, ctx, result.cart)
            state.carts[pid] = V.enrich(result.cart, state)
    # Track what WE touched this session ONLY when the write landed (else nothing changed server-side).
    if ok:
        sa = state.session_added.setdefault(pid, set())
        for o in ops:
            prod = state.catalog.by_ref(o["ref"])
            if prod is None:
                continue
            sa.discard(prod.item_id) if o["mode"] == "remove" else sa.add(prod.item_id)
    return V.cart_atts(pid, state.cart(pid), state), ok, error


async def _clear_cart(state, pid: str) -> None:
    """Empty an app's whole cart via the adapter, then confirm the REAL state (don't fake empty)."""
    from core.agent.contracts import Cart

    adapter = REGISTRY.provider(pid)
    cart = Cart(provider=pid)
    try:
        async with adapter.open_session(state.user_id) as s:
            ctx = await CONTEXT.exec_ctx(adapter, s, state)
            await adapter.ensure_context(s, ctx)
            await adapter.clear(s, ctx)
            res = await adapter.read_cart(s, ctx)
            if res.ok and res.cart is not None:
                cart = V.enrich(res.cart, state)
    except Exception as e:
        log.debug("AGENT CLEAR %s failed: %s", pid, e)
    state.carts[pid] = cart
    state.session_added.pop(pid, None)


async def handle(state, intent: dict[str, Any], emit: Emit = noop) -> dict[str, Any]:
    connected = _connected(state.user_id)
    if not connected:
        return {
            "domain": DOMAIN,
            "status": "unsupported",
            "cards": [],
            "attachments": [],
            "error": None,
            "note": _NO_APP,
            "say": _NO_APP,
        }
    message = intent.get("message", "")

    def _apps(a: dict[str, Any]) -> list[str]:
        return [p for p in (a.get("apps") or connected) if p in connected] or connected

    # ── 2) BRAIN: one call → actions + ack ──────────────────────────────────────────────────────
    brain = await grocery_brain(
        {
            "message": message,
            "history": state.recent_history(),
            "connected_apps": connected,
            "notepad": {pid: state.catalog.structured(pid).get(pid, {}) for pid in connected},
            "cart": _cart_compact(state, connected),
            "old_items": _old_items(state, connected) or None,
            "recent_ui_actions": list(state.recent_ui_actions),
        }
    )
    actions = brain["actions"]
    log.debug("BRAIN ops=%s ack=%s", short([a.get("op") for a in actions]), bool(brain["ack"]))
    if brain["ack"]:
        await emit(say(brain["ack"]))  # instant "what I'm doing" line, before the slow MCP work

    cards_out: list[dict[str, Any]] = []
    atts_out: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []

    # ── 3a) SEARCH (from search ops + selector-add queries) — one merged batch per provider ──────
    selector_adds = [a for a in actions if a.get("op") == "add" and not a.get("ref")]
    search_by_prov: dict[str, list[str]] = {}
    for a in actions:
        q = (a.get("query") or "").strip()
        if (a.get("op") == "search" or a in selector_adds) and q:
            for pid in _apps(a):
                if q.lower() not in {x.lower() for x in search_by_prov.get(pid, [])}:
                    search_by_prov.setdefault(pid, []).append(q)
    for pid, queries in search_by_prov.items():
        pc, pa = await S._search_one(state, pid, queries, None, emit)
        cards_out += pc
        atts_out += pa
        for q in queries:
            outcomes.append(
                {"app": pid, "action": "search", "query": q, "found": _found(state, pid, q)}
            )

    # ── 3b) build the cart-op batch per provider: ref-adds/set_qty/remove + resolved selector-adds ─
    by_prov: dict[str, list[dict[str, Any]]] = {}
    for a in actions:
        op, ref = a.get("op"), a.get("ref", "")
        if op not in ("add", "set_qty", "remove") or not ref:
            continue
        prod = state.catalog.by_ref(ref)
        if prod is None:
            continue
        mode = {"add": "add", "set_qty": "set", "remove": "remove"}[op]
        by_prov.setdefault(prod.provider, []).append(
            {"ref": ref, "qty": int(a.get("qty", 1)), "mode": mode}
        )
    for a in selector_adds:
        q, sel, qty = (
            (a.get("query") or "").strip(),
            a.get("select") or "cheapest",
            int(a.get("qty", 1)),
        )
        for pid in _apps(a):
            ref = _resolve_selector(state, pid, q, sel)
            if ref:
                by_prov.setdefault(pid, []).append({"ref": ref, "qty": qty, "mode": "add"})
            else:
                outcomes.append({"app": pid, "action": "not_found", "query": q})

    # ── 3c) execute cart ops per provider, record one factual outcome each ───────────────────────
    for pid, ops in by_prov.items():
        if pid not in connected:
            continue
        atts, ok, error = await _apply_cart(state, pid, ops)
        atts_out += atts
        attempted = [p.name for o in ops if (p := state.catalog.by_ref(o["ref"]))]
        if not ok:
            # The write did NOT land (e.g. store closed / address not serviceable) — report the
            # truth + reason so the responder never claims a false "added".
            outcomes.append(
                {
                    "app": pid,
                    "action": "cart_failed",
                    "attempted": attempted,
                    "reason": user_message(error) if error else "Cart update nahi ho paya.",
                    "cart": _cart_facts(state, pid),
                }
            )
            continue
        if state.cart(pid).items:
            state.active_provider = pid
        oc: dict[str, Any] = {"app": pid, "action": "cart_update", "cart": _cart_facts(state, pid)}
        added, removed, changed = [], [], []
        for o in ops:
            prod = state.catalog.by_ref(o["ref"])
            if prod is None:
                continue
            if o["mode"] == "remove":
                removed.append(prod.name)
            elif o["mode"] == "set":
                changed.append(f"{prod.name} → {o['qty']}")
            else:
                added.append(f"{prod.name} (₹{_money(prod.price_minor)})")
        if added:
            oc["added"] = added
        if removed:
            oc["removed"] = removed
        if changed:
            oc["changed"] = changed
        outcomes.append(oc)

    # ── 3d) CLEAR ────────────────────────────────────────────────────────────────────────────────
    for a in actions:
        if a.get("op") != "clear":
            continue
        for pid in _apps(a):
            before = [i.name for i in state.cart(pid).items]
            await _clear_cart(state, pid)
            atts_out += V.cart_atts(pid, state.cart(pid), state)
            outcomes.append(
                {"app": pid, "action": "clear", "removed": before, "cart": _cart_facts(state, pid)}
            )

    # ── 3e) SHOW CART ────────────────────────────────────────────────────────────────────────────
    show_apps: list[str] = []
    for a in actions:
        if a.get("op") == "show_cart":
            show_apps += _apps(a)
    for pid in dict.fromkeys(show_apps):
        await V.read_into_cache(state, pid)
        atts_out += V.cart_atts(pid, state.cart(pid), state)
        outcomes.append(
            {"app": pid, "action": "show_cart", "cart": _cart_facts(state, pid, items=True)}
        )

    # ── 3f) CHECKOUT (gated — never places a real order) ─────────────────────────────────────────
    for a in actions:
        if a.get("op") == "checkout":
            for pid in _apps(a):
                outcomes.append(
                    {
                        "app": pid,
                        "action": "checkout",
                        "blocked": True,
                        "note": "Checkout abhi enable nahi — order place NAHI hua. Coming soon.",
                    }
                )

    # ── 4) RESPONSE: narrate from the real outcomes (+ message/history) ──────────────────────────
    say_text = await grocery_response(
        {
            "message": message,
            "history": state.recent_history(),
            "connected_apps": connected,
            "outcomes": outcomes,
            "notepad": {pid: state.catalog.structured(pid).get(pid, {}) for pid in connected},
            "cart": _cart_compact(state, connected),
            "old_items": _old_items(state, connected) or None,
        }
    )
    log.debug("RESPONSE → %s", short(say_text, 120))

    # Old leftover items get surfaced ONCE — now that the responder has had its chance, flag those apps.
    for pid in _old_items(state, connected):
        state.old_items_flagged.add(pid)
    state.recent_ui_actions.clear()  # consumed this turn

    return {
        "domain": DOMAIN,
        "status": "ok",
        "error": None,
        "cards": cards_out,
        "attachments": atts_out,
        "say": say_text,
        **V.addr_keys(state, connected),
    }
