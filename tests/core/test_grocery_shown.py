"""Store-only-what-we-show + Instamart variant grouping (shown_for + products_attachment)."""

from __future__ import annotations

from core.agent.contracts import Product
from core.agent.domains import grocery_view as V
from core.agent.legacy_cards import products_attachment


def _ip(parent, spin, rupees=50, name="Item"):
    return Product(
        provider="instamart",
        item_id=spin,
        name=name,
        price_minor=int(rupees * 100),
        extra={"parent_product_id": parent},
    )


def _zp(spin, rupees=30):
    return Product(provider="zepto", item_id=spin, name="Milk", price_minor=int(rupees * 100))


# ---------------- shown_for ----------------
def test_instamart_caps_8_products_keeps_all_variants():
    prods = []
    for i in range(10):  # 10 distinct products, 2 variants each
        prods += [_ip(f"par{i}", f"sp{i}a"), _ip(f"par{i}", f"sp{i}b", 60)]
    shown = V.shown_for("instamart", prods)
    parents = {p.extra["parent_product_id"] for p in shown}
    assert len(parents) == 8  # only the first 8 PRODUCTS
    assert len(shown) == 16  # but all their variants (8 products x 2)
    assert "par8" not in parents and "par9" not in parents


def test_instamart_dedups_by_item_id():
    prods = [_ip("par0", "sp0"), _ip("par0", "sp0")]  # promoted + organic dup
    assert len(V.shown_for("instamart", prods)) == 1


def test_zepto_caps_12_flat():
    prods = [_zp(f"z{i}") for i in range(20)]
    out = V.shown_for("zepto", prods)
    assert len(out) == 12 and all(p.provider == "zepto" for p in out)


# ---------------- attachment grouping ----------------
def test_free_delivery_gap_below_minimum():
    from core.agent.contracts import Cart, CartItem

    cart = Cart(
        provider="instamart", items=[CartItem(item_id="a", name="x", qty=1, price_minor=6900)]
    )
    fd = V.free_delivery("instamart", cart)
    assert fd["min"] == "₹99" and fd["item_total"] == "₹69"
    assert fd["gap"] == "₹30" and fd["qualifies"] is False  # 99 - 69 = 30


def test_free_delivery_qualifies_above_minimum():
    from core.agent.contracts import Cart, CartItem

    cart = Cart(provider="zepto", items=[CartItem(item_id="a", name="x", qty=1, price_minor=12000)])
    fd = V.free_delivery("zepto", cart)
    assert fd["qualifies"] is True and fd["gap"] == "₹0"


def test_free_delivery_none_when_empty():
    from core.agent.contracts import Cart

    assert V.free_delivery("instamart", Cart(provider="instamart")) is None


def test_attachment_groups_variants_into_one_card():
    prods = [
        _ip("par0", "sp0a", 65, "Harvest Gold White Bread"),
        _ip("par0", "sp0b", 33, "Harvest Gold White Bread"),  # same parent → same card
        _ip("par1", "sp1", 30, "English Oven"),
    ]
    cards = products_attachment("instamart", "bread", prods)["products"]
    assert len(cards) == 2  # two product cards, not three
    assert len(cards[0]["variants"]) == 2  # both sizes under one card (the picker)
    assert {v["spin_id"] for v in cards[0]["variants"]} == {"sp0a", "sp0b"}
    assert len(cards[1]["variants"]) == 1
