"""Search-result helpers for GroceryDomainHandler — filtering + the rendered/stored candidate set.

Split out of grocery_view.py for the file-size budget. All stateless and provider-agnostic (they
operate on the normalized Product contract, so every grocery provider uses them uniformly):
- FILTERING: price ceiling/floor + sort ("300 ke neeche", "sasta wala pehle").
- `shown_for`: the variant-level set we actually render AND store ("store only what we show").
- `product_card`: the normalized product-list card the FE/narrator render.
"""

from __future__ import annotations

from typing import Any

from core.agent.contracts import fmt_minor

_SORTS = {"cheapest", "costliest"}


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
