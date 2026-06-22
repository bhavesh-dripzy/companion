"""Deterministic tests for the grocery price/sort filter + resolution ranking (no LLM/MCP).

Guards the POC-driven behaviour: client-side price filtering on the normalized `price_minor`
(both Instamart & Zepto expose no native filter), and the name-overlap safety net that keeps
'amul bread' from resolving to 'amul milk'.
"""

from __future__ import annotations

from core.agent.contracts import Product
from core.agent.domains import grocery_resolve as R
from core.agent.domains import grocery_view as V


def _p(name, rupees, brand=""):
    return Product(
        provider="instamart", item_id=name, name=name, price_minor=int(rupees * 100), brand=brand
    )


# ---------------- filter_from_intent ----------------
def test_filter_from_intent_price_max():
    flt = V.filter_from_intent({"filters": {"price_max": 300, "price_min": None, "sort": None}})
    assert flt == {"price_max_minor": 30000, "price_min_minor": None, "sort": None}


def test_filter_from_intent_cheapest():
    flt = V.filter_from_intent(
        {"filters": {"price_max": None, "price_min": None, "sort": "cheapest"}}
    )
    assert flt["sort"] == "cheapest"


def test_filter_from_intent_none_when_absent():
    assert V.filter_from_intent({"filters": None}) is None
    assert V.filter_from_intent({}) is None
    assert (
        V.filter_from_intent({"filters": {"price_max": None, "price_min": None, "sort": "bogus"}})
        is None
    )


# ---------------- apply_filter ----------------
def test_apply_filter_price_ceiling_sorts_ascending():
    prods = [_p("a", 50), _p("b", 12), _p("c", 400), _p("d", 20)]
    flt = V.filter_from_intent({"filters": {"price_max": 300, "price_min": None, "sort": None}})
    out = V.apply_filter(prods, flt)
    assert [p.name for p in out] == ["b", "d", "a"]  # 400 dropped, rest ascending


def test_apply_filter_costliest_first():
    prods = [_p("a", 50), _p("b", 12), _p("d", 20)]
    flt = V.filter_from_intent(
        {"filters": {"price_max": None, "price_min": None, "sort": "costliest"}}
    )
    assert [p.name for p in V.apply_filter(prods, flt)] == ["a", "d", "b"]


def test_apply_filter_empty_when_nothing_in_range():
    prods = [_p("a", 50), _p("b", 80)]
    flt = V.filter_from_intent({"filters": {"price_max": 15, "price_min": None, "sort": None}})
    assert V.apply_filter(prods, flt) == []


def test_apply_filter_noop_without_filter():
    prods = [_p("a", 50), _p("b", 12)]
    assert V.apply_filter(prods, None) is prods


def test_product_card_flags_empty_after_filter():
    flt = V.filter_from_intent({"filters": {"price_max": 1, "price_min": None, "sort": None}})
    card = V.product_card("instamart", "chips", [], flt)
    assert card["empty_after_filter"] is True
    assert card["filter"]  # human label present


# ---------------- name-overlap safety net (bugfix #4) ----------------
def test_name_candidates_prefers_higher_overlap():
    compact = [
        {"ref": "p1", "name": "Amul Taaza Milk 1L", "brand": "Amul"},
        {"ref": "p2", "name": "Amul Gold Milk 500ml", "brand": "Amul"},
        {"ref": "p3", "name": "Amul Bread 400g", "brand": "Amul"},
    ]
    # "amul bread" overlaps 2 tokens with p3, 1 with the milks → p3 only
    assert R.name_candidates(compact, "amul bread") == ["p3"]


def test_fallback_single_match_resolves():
    compact = [{"ref": "p3", "name": "Amul Bread", "brand": "Amul"}]
    res = R.fallback(compact, [{"phrase": "amul bread", "qty": 1}])
    assert res["resolved"] == [{"ref": "p3", "qty": 1}]
    assert not res["ambiguous"] and not res["unresolved"]


# ---------------- deterministic price superlatives ----------------
def _c(ref, name, price):
    return {"ref": ref, "name": name, "brand": "", "price": price}


def test_superlative_cheapest_picks_lowest():
    compact = [_c("p1", "bread", 65), _c("p2", "bread", 33), _c("p3", "bread", 50)]
    assert R.superlative_ref(compact, "sabse sasta bread") == "p2"
    assert R.superlative_ref(compact, "cheapest bread") == "p2"


def test_superlative_costliest_picks_highest():
    compact = [_c("p1", "bread", 65), _c("p2", "bread", 33), _c("p3", "bread", 50)]
    assert R.superlative_ref(compact, "sabse mehnga bread") == "p1"


def test_superlative_scopes_to_name():
    # "cheapest milk" ignores the cheaper bread
    compact = [_c("b1", "bread", 20), _c("m1", "milk", 30), _c("m2", "milk", 45)]
    assert R.superlative_ref(compact, "sabse sasta milk") == "m1"


def test_no_superlative_returns_none():
    compact = [_c("p1", "bread", 65)]
    assert R.superlative_ref(compact, "amul bread") is None
