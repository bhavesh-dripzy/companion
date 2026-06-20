"""Deterministic target resolution for a planner-built Zepto `update_cart`.

The planner reliably builds the FIRST add (a same-turn search supplies real ids),
but for a mutation that targets an item ALREADY in the cart — "make it 3", "remove
the milk", "set it to 0" — it emits a placeholder pvid (`__ZEPTO_PVID__`, cleared
across turns) or the first search hit, neither of which is the cart line. The
unresolved-placeholder check then fails the whole call, or the wrong product is
mutated (a no-op the narrator reports as success).

This resolves each requested line to a REAL (pvid, spid) deterministically — by
the named item in the live cart, the sole cart line for an unnamed tweak, a valid
supplied id, or the named item among products shown this session — so cart edits
work without a fresh search and always hit the right item. It also clamps the
quantity to available stock. Pure functions of the session bag; no I/O.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "my",
        "of",
        "to",
        "in",
        "and",
        "with",
        "for",
        "please",
        "add",
        "remove",
        "delete",
        "set",
        "change",
        "make",
        "it",
        "them",
        "that",
        "this",
        "from",
        "cart",
        "zepto",
        "qty",
        "quantity",
        "pack",
        "fresh",
        "pcs",
        "piece",
        "pieces",
        "x",
        "no",
        "now",
        "just",
        "only",
        "more",
        "less",
    }
)


def _tokens(text: Any) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if t not in _STOP and len(t) > 1
    }


def _entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a cart line / product row to {pvid, spid, name, available}."""
    if not isinstance(row, dict):
        return None
    pvid = str(row.get("product_variant_id") or row.get("productVariantId") or "").strip()
    spid = str(row.get("store_product_id") or row.get("storeProductId") or "").strip()
    if not pvid:
        return None
    avail = row.get("available_quantity")
    return {
        "pvid": pvid,
        "spid": spid,
        "name": str(row.get("name") or "").strip(),
        "available": avail if isinstance(avail, int) and avail > 0 else None,
    }


def _entries(rows: Any) -> list[dict[str, Any]]:
    return [e for e in (_entry(r) for r in rows or []) if e] if isinstance(rows, list) else []


def cart_entries(session: dict[str, Any]) -> list[dict[str, Any]]:
    cart = session.get("session_zepto_cart") if isinstance(session, dict) else None
    return _entries(cart.get("items") if isinstance(cart, dict) else None)


def product_entries(session: dict[str, Any]) -> list[dict[str, Any]]:
    return _entries(session.get("session_zepto_products") if isinstance(session, dict) else None)


def _best_match(name: str, message: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The candidate whose name best overlaps the requested name (or, failing a
    name, the user message). Ties break to the first — search/cart order."""
    want = _tokens(name) or _tokens(message)
    if not want:
        return None
    best, score = None, 0
    for cand in candidates:
        overlap = len(want & _tokens(cand["name"]))
        if overlap > score:
            best, score = cand, overlap
    return best if score else None


def _supplied(
    pvid: str, valid_ids: dict[str, str], pool: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """A real, non-placeholder pvid the planner gave that we can vouch for.

    Prefers the pool entry (it carries name + available stock); falls back to the
    valid (pvid -> spid) map so a freshly-searched id with no row still resolves."""
    if not pvid or _PLACEHOLDER.fullmatch(pvid):
        return None
    for cand in pool:
        if cand["pvid"] == pvid:
            if not cand["spid"] and pvid in valid_ids:
                return {**cand, "spid": valid_ids[pvid]}
            return cand
    if pvid in valid_ids:
        return {"pvid": pvid, "spid": valid_ids[pvid], "name": "", "available": None}
    return None


def _resolve_one(
    item: dict[str, Any],
    qty: int,
    *,
    message: str,
    cart: list[dict[str, Any]],
    products: list[dict[str, Any]],
    valid_ids: dict[str, str],
) -> dict[str, Any] | None:
    """The real cart target for one requested line, or None when undecidable."""
    pvid = str(item.get("productVariantId") or item.get("product_variant_id") or "").strip()
    name = str(item.get("name") or "").strip()
    removal = qty <= 0
    placeholder = not pvid or bool(_PLACEHOLDER.fullmatch(pvid))
    # (a) the named item already in the cart -> edit/remove THAT line (fixes the
    #     wrong-target no-op: a placeholder/first-search pvid is overridden here).
    target = _best_match(name, message, cart)
    if target:
        return target
    # (b) an unnamed tweak ("make it 3" / "remove it") with a single cart line.
    #     Only when the request gives no usable id of its own (removal or a bare
    #     placeholder) — a fabricated *add* must never hijack the sole cart line.
    if not _tokens(name) and len(cart) == 1 and (removal or placeholder):
        return cart[0]
    # (c) a real supplied id we can vouch for (normal add path).
    if not removal:
        target = _supplied(pvid, valid_ids, products + cart)
        if target:
            return target
        # (d) the named item among products shown this session.
        target = _best_match(name, message, products)
        if target:
            return target
    return None


def resolve_cart_items(
    args: dict[str, Any],
    session: dict[str, Any],
    *,
    valid_ids: dict[str, str],
    device_id: str | None,
    message: str,
) -> None:
    """Rewrite `args` in place: every cartItem gets a real (pvid, spid), the
    quantity is clamped to stock, deviceId is pinned, and replaceCart is dropped
    (Zepto's default MERGE keeps the rest of the cart). Lines that can't be
    resolved to a real product are dropped — skipping beats a wrong/failed write."""
    if device_id:
        args["deviceId"] = device_id
    args.pop("replaceCart", None)
    cart = cart_entries(session)
    products = product_entries(session)
    out: list[dict[str, Any]] = []
    for it in args.get("cartItems") or []:
        if not isinstance(it, dict):
            continue
        try:
            qty = int(it.get("quantity"))
        except (TypeError, ValueError):
            qty = 1
        target = _resolve_one(
            it, qty, message=message, cart=cart, products=products, valid_ids=valid_ids
        )
        if target is None:
            continue
        spid = target["spid"] or valid_ids.get(target["pvid"], "")
        if qty <= 0:
            out.append({"productVariantId": target["pvid"], "storeProductId": spid, "quantity": 0})
            continue
        if target.get("available") and qty > target["available"]:
            qty = target["available"]
        line = {"productVariantId": target["pvid"], "storeProductId": spid, "quantity": qty}
        for k in ("name", "price", "mrp", "imageUrl", "packSize"):
            if it.get(k) is not None:
                line[k] = it[k]
        out.append(line)
    # Always replace the list (never leave an unresolved/fabricated id behind — an
    # empty list is a safe server no-op; a bogus pvid would fail or mutate wrong).
    if "cartItems" in args:
        args["cartItems"] = out
