"""switch_context — change the grocery delivery address from chat.

Lists the provider's saved addresses, matches the user's phrase via the LLM resolver (same role
products use), validates SERVICEABILITY, then pins the new address. Serviceability differs by
provider:
- Zepto exposes a read-only check (`get_location_serviceability`) → validate, then commit.
- Instamart has NO read-only check; the ONLY signal is `update_cart`, which also POISONS the cart's
  address pointer on a non-serviceable address (POC §4b). So we probe by moving the cart to the new
  address and, on failure, re-pin the old (last-good) address to un-poison — never leaving a bricked
  cart. The cart itself is otherwise read from memory (per product decision).
"""

from __future__ import annotations

import contextlib
from typing import Any

from core.agent import address_store
from core.agent.debug import log, short
from core.agent.domains import grocery_view as V
from core.agent.llm import resolve
from core.agent.providers.base import SERVICEABLE, ExecCtx
from core.agent.registry import REGISTRY

DOMAIN = "grocery"


def _short_label(label: str) -> str:
    return label.split(" · ")[0] if label else label


def _note(state, pid: str, msg: str) -> dict[str, Any]:
    # say=msg → the address outcome is spoken VERBATIM (deterministic), never re-narrated by the
    # legacy narrator (which could misread an empty post-switch cart as "cart clear kar diya").
    return V.cart_result(
        state, pid, state.cart(pid), note=msg, say=msg, **V.addr_keys(state, [pid])
    )


async def _match(addrs: list[dict[str, Any]], phrase: str) -> dict[str, Any] | None:
    if not phrase:
        return None
    compact = [
        {
            "ref": a["id"],
            "name": a["label"],
            "brand": "",
            "variation": "",
            "price": 0,
            "in_cart": False,
        }
        for a in addrs
    ]
    refs = await resolve(compact, [{"phrase": phrase, "qty": 1}])
    res = refs.get("resolved") or []
    if res:
        rid = res[0].get("ref")
        hit = next((a for a in addrs if a["id"] == rid), None)
        if hit:
            return hit
    # deterministic fallback: token overlap on the label
    toks = [t for t in phrase.lower().split() if len(t) > 2]
    if not toks:
        return None
    exact = [a for a in addrs if all(t in a["label"].lower() for t in toks)]
    if exact:
        return exact[0]
    loose = [a for a in addrs if any(t in a["label"].lower() for t in toks)]
    return loose[0] if len(loose) == 1 else None


async def _probe_move(adapter, s, state, pid, new_ctx, old_addr) -> bool:
    """Instamart: moving the cart to `new_ctx` is the only serviceability signal — and it poisons
    the cart on a non-serviceable address. Returns True if serviceable (cart moved + committed in
    memory); False if not (cart re-pinned to `old_addr` to un-poison). Empty cart → nothing to
    poison → treat as ok (the next add validates via the strategy)."""
    items = [(i.item_id, i.qty, i.secondary_id) for i in state.cart(pid).items]
    if not items:
        return True
    res = await adapter.write_cart(s, items, new_ctx, replace=True)
    if res.ok:
        if res.cart is not None:
            state.carts[pid] = V.enrich(res.cart, state)
        return True
    log.debug("SWITCH %s probe FAILED (%s) → re-pin old address %s", pid, res.error, old_addr)
    if old_addr:  # un-poison: re-send the same items to the last-good address
        old_ctx = ExecCtx(
            user_id=state.user_id, address_id=old_addr, device_id=f"companion-{state.user_id}"
        )
        with contextlib.suppress(Exception):
            await adapter.write_cart(s, items, old_ctx, replace=True)
    return False


async def switch_context(state, intent, pid: str) -> dict[str, Any]:
    adapter = REGISTRY.provider(pid)
    # A UI picker carries the EXACT address id → pin it directly (no LLM match). A chat phrase
    # ("ghar wale address pe") still resolves via the resolver / token fallback.
    direct_id = intent.get("address_id")
    phrase = (intent.get("item_refs") or [{}])[0].get("phrase") or intent.get("raw_query", "")
    old_addr = (state.provider_session.get(pid) or {}).get("address_id")
    async with adapter.open_session(state.user_id) as s:
        addrs = await adapter.list_addresses(s)
        if not addrs:
            return _note(state, pid, "Is app mein koi saved address nahi mila.")
        chosen = (
            next((a for a in addrs if str(a.get("id")) == str(direct_id)), None)
            if direct_id
            else await _match(addrs, phrase)
        )
        if chosen is None:
            opts = ", ".join(_short_label(a["label"]) for a in addrs[:6])
            return _note(
                state, pid, f"Woh address samajh nahi aaya. Aapke saved addresses: {opts}."
            )
        label = _short_label(chosen["label"])
        ctx = ExecCtx(
            user_id=state.user_id,
            address_id=chosen["id"],
            lat=chosen.get("lat"),
            lng=chosen.get("lng"),
            device_id=f"companion-{state.user_id}",
        )
        # --- serviceability gate (the cart-poison guard) ---
        if adapter.has(SERVICEABLE):
            await adapter.ensure_context(s, ctx)  # Zepto: pins the store to this address
            ok, _msg = await adapter.serviceable(s, ctx)  # read-only check
            if not ok:
                return _note(state, pid, f"'{label}' par yeh app abhi deliver nahi karta.")
        elif not await _probe_move(adapter, s, state, pid, ctx, old_addr):
            return _note(
                state,
                pid,
                f"'{label}' par Swiggy abhi deliver nahi karta — address waisa hi rakha hai.",
            )
    # Pin the new address on THIS provider only (per-provider source of truth) — never a shared
    # per-domain field, so switching one app's address can't touch the other's.
    state.provider_session[pid] = {
        "address_id": chosen["id"],
        "lat": chosen.get("lat"),
        "lng": chosen.get("lng"),
        "device_id": f"companion-{state.user_id}",
        "label": chosen["label"],
    }
    # Persist to the GLOBAL store (DynamoDB-backed) so the selection survives restarts and is
    # consistent across containers — every later turn hydrates from here.
    address_store.set(state.user_id, pid, {"address_id": chosen["id"], "label": chosen["label"]})
    # Server is source of truth: read (+ price) the live cart AT THE NEW ADDRESS so "show my updated
    # cart" reflects reality, not a stale/empty in-memory copy (e.g. after a restart). Both providers.
    await V.read_into_cache(state, pid)
    log.debug("SWITCH %s → address=%s (%s)", pid, chosen["id"], short(chosen["label"]))
    msg = f"Address badal diya — ab delivery {_short_label(chosen['label'])} par hogi."
    return V.cart_result(
        state,
        pid,
        state.cart(pid),
        note=msg,
        say=msg,
        **V.addr_keys(state, [pid]),
    )
