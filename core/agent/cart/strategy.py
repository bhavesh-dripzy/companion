"""CartStrategy — per-provider cart semantics.

`set_items(adapter, session, last_good, delta, meta, ctx)`:
  - `last_good`: the authoritative cart held in state (source of truth, never `get_cart` after a write).
  - `delta`:    {item_id -> target_qty} (absolute; 0 = remove).
  - `meta`:     {item_id -> {name, secondary_id, price_minor}} from catalog/cart (for write args + display).
Returns a `CartResult`. The strategy owns cart-integrity (repair); the DomainHandler interprets the
ErrorKind (MAX_QTY → tell cap, STALE_ITEM → re-search, NOT_SERVICEABLE → context flow).
"""

from __future__ import annotations

import contextlib

from core.agent.contracts import Cart, CartResult
from core.agent.debug import log, short


def _meta_sid(meta: dict[str, dict], iid: str) -> str:
    return str((meta.get(iid) or {}).get("secondary_id", "") or "")


class CartStrategy:
    name = "base"

    async def set_items(
        self, adapter, session, last_good: Cart, delta: dict[str, int], meta: dict[str, dict], ctx
    ) -> CartResult:
        raise NotImplementedError


class ReplaceAllFragile(CartStrategy):
    """Instamart: send the FULL desired set every time; a failed write may corrupt the server
    cart, so on ANY error re-pin `last_good` (repair) before surfacing the classified failure."""

    name = "replace_all_fragile"

    async def set_items(self, adapter, session, last_good, delta, meta, ctx) -> CartResult:
        desired: dict[str, int] = {i.item_id: i.qty for i in last_good.items}
        desired.update(delta)
        desired = {iid: q for iid, q in desired.items() if q and q > 0}
        if not desired:  # removing the last item → clear (empty items is rejected)
            await adapter.clear(session, ctx)
            return CartResult.success(Cart(provider=adapter.id))
        items = [(iid, q, _meta_sid(meta, iid)) for iid, q in desired.items()]

        log.debug("  MCP write_cart(%s) replace-all desired=%s", adapter.id, short(desired))
        res = await adapter.write_cart(session, items, ctx, replace=True)
        if res.ok:
            return res
        # REPAIR: re-send last_good to un-corrupt the server cart (best effort).
        log.debug(
            "  MCP write FAILED (%s) → REPAIR re-pin last_good",
            res.error.value if res.error else "?",
        )
        good = [(i.item_id, i.qty, i.secondary_id) for i in last_good.items]
        if good:
            with contextlib.suppress(Exception):
                await adapter.write_cart(session, good, ctx, replace=True)
        return res


class UpsertRobust(CartStrategy):
    """Zepto: send only the DELTA (qty 0 removes); server is authoritative & robust, no repair."""

    name = "upsert_robust"

    async def set_items(self, adapter, session, last_good, delta, meta, ctx) -> CartResult:
        items = [(iid, q, _meta_sid(meta, iid)) for iid, q in delta.items()]
        if not items:
            return CartResult.success(last_good)
        log.debug("  MCP update_cart(%s) upsert delta=%s", adapter.id, short(delta))
        return await adapter.write_cart(session, items, ctx, replace=False)


STRATEGIES: dict[str, CartStrategy] = {
    ReplaceAllFragile.name: ReplaceAllFragile(),
    UpsertRobust.name: UpsertRobust(),
}


def strategy_for(name: str) -> CartStrategy:
    return STRATEGIES.get(name, STRATEGIES["replace_all_fragile"])
