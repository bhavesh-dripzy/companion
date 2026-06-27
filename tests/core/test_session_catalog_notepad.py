"""SessionCatalog structured-notepad tests.

The notepad (per provider → item-category → price-ranked refs) is ADDITIVE: the flat ref index
(by_ref / slice / compact) that taps + the Resolver use must keep working exactly as before.
"""

from __future__ import annotations

from core.agent.contracts import Product
from core.agent.state import SessionCatalog


def _p(provider: str, item_id: str, name: str, price_inr: int, brand: str = "") -> Product:
    return Product(
        provider=provider, item_id=item_id, name=name, price_minor=price_inr * 100, brand=brand
    )


def test_bucket_is_price_sorted_and_cheapest_is_first():
    cat = SessionCatalog()
    cat.append(
        [
            _p("instamart", "b2", "Harvest Gold Brown", 55),
            _p("instamart", "b1", "NOICE Brown Bread", 49),
            _p("instamart", "b3", "English Oven Brown", 60),
        ],
        query="brown bread",
    )
    bucket = cat.bucket("instamart", "brown bread")
    assert [p.name for _, p in bucket] == ["NOICE Brown Bread", "Harvest Gold Brown", "English Oven Brown"]
    ref, prod = cat.cheapest("instamart", "brown bread")
    assert prod.name == "NOICE Brown Bread"
    assert cat.by_ref(ref) is prod  # flat view still resolves the same product


def test_category_key_is_normalized():
    cat = SessionCatalog()
    cat.append([_p("instamart", "s1", "Vikram Sooji", 28)], query="Brown   BREAD")
    # Different casing/spacing of the same query hits the same bucket.
    assert cat.cheapest("instamart", "brown bread") is not None


def test_re_search_replaces_the_bucket():
    cat = SessionCatalog()
    cat.append([_p("instamart", "b1", "Old Bread", 40)], query="bread")
    cat.append([_p("instamart", "b9", "New Bread", 45)], query="bread")
    bucket = cat.bucket("instamart", "bread")
    assert [p.name for _, p in bucket] == ["New Bread"]  # latest search is the current shelf


def test_providers_and_categories_are_independent():
    cat = SessionCatalog()
    cat.append([_p("instamart", "b1", "NOICE", 49)], query="bread")
    cat.append([_p("zepto", "z1", "English Oven", 52)], query="bread")
    cat.append([_p("instamart", "s1", "Vikram", 28)], query="sooji")
    assert cat.cheapest("instamart", "bread")[1].name == "NOICE"
    assert cat.cheapest("zepto", "bread")[1].name == "English Oven"
    assert cat.cheapest("instamart", "sooji")[1].name == "Vikram"
    assert set(cat.categories("instamart")) == {"bread", "sooji"}


def test_structured_projection_shape():
    cat = SessionCatalog()
    cat.append([_p("instamart", "b2", "B", 55), _p("instamart", "b1", "A", 49)], query="bread")
    cat.append([_p("zepto", "z1", "Z", 52)], query="bread")
    note = cat.structured()
    assert set(note.keys()) == {"instamart", "zepto"}
    assert [i["name"] for i in note["instamart"]["bread"]] == ["A", "B"]  # price-sorted
    assert note["instamart"]["bread"][0]["price"] == 49.0
    # scoped to one provider
    assert set(cat.structured("zepto").keys()) == {"zepto"}


def test_append_without_query_is_flat_only():
    cat = SessionCatalog()
    refs = cat.append([_p("instamart", "x1", "Cart Item", 30)])  # e.g. cart enrichment
    assert cat.by_ref(refs[0]).name == "Cart Item"  # flat view populated
    assert cat.categories("instamart") == []  # no bucket created
