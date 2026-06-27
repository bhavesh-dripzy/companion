"""Search-result helpers for GroceryDomainHandler — filtering + the rendered/stored candidate set.

Split out of grocery_view.py for the file-size budget. All stateless and provider-agnostic (they
operate on the normalized Product contract, so every grocery provider uses them uniformly):
- FILTERING: price ceiling/floor + sort ("300 ke neeche", "sasta wala pehle").
- `shown_for`: the variant-level set we actually render AND store ("store only what we show").
- `product_card`: the normalized product-list card the FE/narrator render.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.agent.context import CONTEXT
from core.agent.contracts import fmt_minor
from core.agent.debug import log, short
from core.agent.events import Emit, cards, noop, say
from core.agent.legacy_cards import products_attachment
from core.agent.registry import REGISTRY

_SORTS = {"cheapest", "costliest"}
_LABEL = {"instamart": "Instamart", "zepto": "Zepto", "swiggy_food": "Swiggy", "zomato": "Zomato"}


def action_note(action: str, meta: dict[str, dict], delta: dict[str, int]) -> str | None:
    """A deterministic, factual one-liner stating exactly what a cart write just did (item + qty),
    so the narrator grounds an add/qty-change/remove on FACT instead of guessing from history (which
    is what made tap-adds and set_qty narrate the wrong/previous turn). `meta` carries each written
    item's name (it works for both NL-resolved and deterministic taps); `delta` carries the qty."""
    names = ", ".join(m["name"] for m in meta.values() if m.get("name"))
    if not names:
        return None
    qty = next(iter(delta.values()), 1)
    if (
        action == "remove" or qty == 0
    ):  # a cart minus down to 0 arrives as set_qty qty:0 = a removal
        return f"{names} cart se hata diya."
    if action == "set_qty":
        return f"{names} ki quantity {qty} kar di."
    return f"{names} cart mein add kar diya."


# ---------------- search-result filtering (price / sort) ----------------
def filter_from_intent(intent: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize the Router's optional `filters` block into minor-unit bounds + sort, or None.

    The Router emits user-stated rupees (`price_max`/`price_min`) and a `sort`; we convert to
    paise here so filtering matches the normalized Product.price_minor. Provider-independent."""
    f = intent.get("filters") or {}
    pmax, pmin, sort = f.get("price_max"), f.get("price_min"), f.get("sort")
    if pmax is None and pmin is None and sort not in _SORTS:
        return None
    return {
        "price_max_minor": int(round(float(pmax) * 100)) if pmax is not None else None,
        "price_min_minor": int(round(float(pmin) * 100)) if pmin is not None else None,
        "sort": sort if sort in _SORTS else None,
    }


def apply_filter(products: list, flt: dict[str, Any] | None) -> list:
    if not flt:
        return products
    lo, hi = flt.get("price_min_minor"), flt.get("price_max_minor")
    out = [p for p in products if p.price_minor]  # price-less items can't be price-filtered
    if hi is not None:
        out = [p for p in out if p.price_minor <= hi]
    if lo is not None:
        out = [p for p in out if p.price_minor >= lo]
    if flt.get("sort") == "cheapest" or hi is not None:
        out.sort(key=lambda p: p.price_minor)
    elif flt.get("sort") == "costliest":
        out.sort(key=lambda p: p.price_minor, reverse=True)
    return out


def shown_for(pid: str, products: list) -> list:
    """The variant-level set we actually render AND store ('store only what we show'). Instamart:
    the first 8 PRODUCTS (grouped by parent_product_id) with ALL their variants; Zepto: the first
    12 (already flat, one variant each). Deduped by item_id, relevance order preserved."""
    seen: set[str] = set()
    if pid != "instamart":
        out = []
        for p in products:
            if p.item_id in seen:
                continue
            seen.add(p.item_id)
            out.append(p)
            if len(out) >= 12:
                break
        return out
    parents: list[str] = []
    by_parent: dict[str, list] = {}
    for p in products:
        if p.item_id in seen:
            continue
        parent = p.extra.get("parent_product_id") or p.item_id
        if parent not in by_parent:
            if len(parents) >= 8:  # cap at 8 distinct products, not variants
                continue
            parents.append(parent)
            by_parent[parent] = []
        by_parent[parent].append(p)
        seen.add(p.item_id)
    return [p for parent in parents for p in by_parent[parent]]


def filter_label(flt: dict[str, Any] | None) -> str:
    if not flt:
        return ""
    parts = []
    if flt.get("price_max_minor") is not None:
        parts.append(f"{fmt_minor(flt['price_max_minor'])} ke neeche")
    if flt.get("price_min_minor") is not None:
        parts.append(f"{fmt_minor(flt['price_min_minor'])} ke upar")
    if flt.get("sort") == "cheapest":
        parts.append("sabse sasta pehle")
    elif flt.get("sort") == "costliest":
        parts.append("sabse mehnga pehle")
    return ", ".join(parts)


def product_card(pid, query, products, flt=None) -> dict[str, Any]:
    label = filter_label(flt)
    return {
        "type": "product_list",
        "provider": pid,
        "title": f"{query} ({label})" if label else query,
        "filter": label or None,
        "empty_after_filter": bool(flt) and not products,
        "items": [
            {
                "ref": p.extra.get("ref", ""),
                "name": p.name,
                "price": fmt_minor(p.price_minor),
                "in_stock": p.in_stock,
            }
            for p in products
        ],
    }


# ---------------- concurrent fan-out search (streams narration + cards per completion) ----------------
async def _search_one(state, pid: str, phrases, flt, emit: Emit):
    """Search ONE provider, store the shown set, then emit its narration + cards the MOMENT it
    finishes — so whichever app completes first narrates first (no hard-coded order). Independent
    failures surface as a per-provider error card without sinking the others. Returns (cards, atts)."""
    adapter = REGISTRY.provider(pid)
    pcards: list[dict[str, Any]] = []
    patts: list[dict[str, Any]] = []
    try:
        async with adapter.open_session(state.user_id) as s:
            ctx = await CONTEXT.exec_ctx(adapter, s, state)
            await adapter.ensure_context(s, ctx)
            for q in [p for p in phrases if p]:
                products = await adapter.search(s, q, ctx)
                shown = shown_for(pid, apply_filter(products, flt))
                # query=q builds the structured notepad bucket (provider, item) for the grounded
                # selection step; the flat ref index (taps/resolver) is unchanged either way.
                state.catalog.append(shown, query=q)  # sync between awaits → safe under concurrency
                log.debug("SEARCH %s q=%r → %d raw, %d shown", pid, q, len(products), len(shown))
                pcards.append(product_card(pid, q, shown, flt))
                if att := products_attachment(pid, q, shown):
                    patts.append(att)
    except Exception as e:
        pcards.append({"type": "error", "provider": pid, "message": f"{type(e).__name__}: {e}"})
    # Merge per-query attachments → ONE provider block (header + address bar once), a section per query.
    if len(patts) > 1:
        merged = [p for a in patts for p in a.get("products", [])]
        patts = [{"kind": patts[0]["kind"], "products": merged}]
    label = _LABEL.get(pid, pid.capitalize())
    await emit(
        say(f"{label} pe ye options mile:" if patts else f"{label} pe kuch khaas nahi mila.")
    )
    await emit(cards(patts, provider=pid))
    return pcards, patts


async def fan_out(state, intent, buckets: dict[str, list], emit: Emit = noop) -> dict[str, Any]:
    """Search each provider in the plan CONCURRENTLY with ITS OWN phrases — so "bread instamart pe
    aur sooji zepto pe" searches bread on Instamart and sooji on Zepto, while a plain "bread" (no
    app) searches bread on both. Each narrates + streams its cards as it finishes (completion order).
    Aggregates into one result for the final/non-streaming payload."""
    from core.agent.domains import grocery_view as V  # deferred: grocery_view re-exports from here

    flt = filter_from_intent(intent)
    raw = intent.get("raw_query", "")
    allq = [r.get("phrase", "") for items in buckets.values() for r in items if r.get("phrase")]
    await emit(say(f"Theek hai, {', '.join(dict.fromkeys(allq)) or 'items'} dhoondta hoon…"))
    parts = await asyncio.gather(
        *(
            _search_one(
                state,
                pid,
                [r.get("phrase", "") for r in items if r.get("phrase")] or [raw],
                flt,
                emit,
            )
            for pid, items in buckets.items()
        )
    )
    log.debug("FAN-OUT %s → %d cards total", short(list(buckets)), sum(len(pc) for pc, _ in parts))
    return {
        "domain": "grocery",
        "status": "ok",
        "cards": [c for pc, _ in parts for c in pc],
        "attachments": [a for _, pa in parts for a in pa],
        "error": None,
        "context_header": V.header(state),
        **V.addr_keys(state, list(buckets)),
    }


async def cart_fan_out(
    handler, state, intent, action, buckets, emit: Emit = noop
) -> dict[str, Any]:
    """A cart op (add/set_qty/remove/clear/show_cart) fanned out across MULTIPLE apps concurrently
    — e.g. "dono carts pe add karo", or "X instamart pe aur Y zepto pe". Each app gets ITS OWN items
    (from the plan `buckets`), resolves against its OWN catalog, writes its OWN cart, and streams its
    note + cart card the moment it finishes (completion order). Aggregated. Scales to N providers."""

    async def one(pid):
        if action == "show_cart":
            res = await handler._show_cart(state, pid)
        elif action == "clear":
            res = await handler._clear(state, pid)
        else:  # add / set_qty / remove — each app gets only ITS items; fan-out streams the note+card
            sub = {**intent, "provider_hint": pid, "item_refs": buckets[pid]}
            res = await handler._cart_op(state, sub, pid, noop)
        label = _LABEL.get(pid, pid.capitalize())
        await emit(say(f"{label} — {res['note']}" if res.get("note") else f"{label}:"))
        await emit(cards(res.get("attachments", []), provider=pid))
        return pid, res

    parts = await asyncio.gather(*(one(pid) for pid in buckets))
    state.active_provider = "both"  # multi-app context (per-app _cart_op overwrote it; reset here)
    pending = next((r["pending_action"] for _, r in parts if r.get("pending_action")), None)
    out = {
        "domain": "grocery",
        "status": "ambiguous" if pending else "ok",
        "error": None,
        "cards": [c for _, r in parts for c in r.get("cards", [])],
        "attachments": [a for _, r in parts for a in r.get("attachments", [])],
        "note": " ".join(
            f"{_LABEL.get(pid, pid.capitalize())} pe {r['note']}"
            for pid, r in parts
            if r.get("note")
        ),
    }
    if pending:
        out["pending_action"] = pending
    return out
