"""Deterministic Zepto cart-target resolution (prod test findings F5/F16/F8/F9).

Mutations that target an item already in the cart must resolve to the real
(pvid, spid) without a fresh same-turn search, hit the RIGHT item, clamp to stock,
and drop fabricated ids."""

import pytest

from core.providers.plugins.zepto.cart_resolve import resolve_cart_items

pytestmark = pytest.mark.component

_DEV = "dev-1"


def _session(cart=None, products=None):
    s = {}
    if cart is not None:
        s["session_zepto_cart"] = {"items": cart}
    if products is not None:
        s["session_zepto_products"] = products
    return s


def _run(cart_items, session, *, valid_ids=None, message=""):
    args = {"cartItems": cart_items}
    resolve_cart_items(
        args, session, valid_ids=valid_ids or {}, device_id=_DEV, message=message
    )
    return args


def test_quantity_change_on_sole_cart_item_without_search():
    # F5: "make it 3" — placeholder pvid, no name, single cart line, no valid_ids.
    cart = [{"product_variant_id": "P1", "store_product_id": "S1", "name": "Amul Taaza Milk"}]
    args = _run(
        [{"productVariantId": "__ZEPTO_PVID__", "quantity": 3}], _session(cart), message="make it 3"
    )
    assert args["cartItems"] == [{"productVariantId": "P1", "storeProductId": "S1", "quantity": 3}]
    assert args["deviceId"] == _DEV


def test_remove_by_name_targets_cart_line_not_first_search_hit():
    # F16: planner resolved the placeholder to the FIRST search hit (PX), but the
    # user means the Amul Taaza already in the cart — name match must win.
    cart = [{"product_variant_id": "P1", "store_product_id": "S1", "name": "Amul Taaza Toned Milk"}]
    products = [
        {"product_variant_id": "PX", "store_product_id": "SX", "name": "Frubon HiLo Milk"},
        {"product_variant_id": "P1", "store_product_id": "S1", "name": "Amul Taaza Toned Milk"},
    ]
    args = _run(
        [{"productVariantId": "PX", "name": "Amul Taaza", "quantity": 0}],
        _session(cart, products),
        valid_ids={"PX": "SX", "P1": "S1"},
        message="remove the amul taaza",
    )
    assert args["cartItems"] == [{"productVariantId": "P1", "storeProductId": "S1", "quantity": 0}]


def test_quantity_clamped_to_available_stock():
    # F8: 99999 with stock 10 -> 10.
    products = [
        {"product_variant_id": "P1", "store_product_id": "S1", "name": "Milk", "available_quantity": 10}
    ]
    args = _run(
        [{"productVariantId": "P1", "name": "Milk", "quantity": 99999}],
        _session([], products),
        valid_ids={"P1": "S1"},
    )
    assert args["cartItems"][0]["quantity"] == 10


def test_negative_quantity_becomes_removal():
    # F9: a negative quantity is a removal, never an add.
    cart = [{"product_variant_id": "P1", "store_product_id": "S1", "name": "Milk"}]
    args = _run([{"productVariantId": "__ZEPTO_PVID__", "quantity": -3}], _session(cart))
    assert args["cartItems"] == [{"productVariantId": "P1", "storeProductId": "S1", "quantity": 0}]


def test_add_new_item_by_name_from_products():
    cart = [{"product_variant_id": "P1", "store_product_id": "S1", "name": "Amul Taaza Milk"}]
    products = [{"product_variant_id": "P2", "store_product_id": "S2", "name": "Mother Dairy Milk"}]
    args = _run(
        [{"productVariantId": "__ZEPTO_PVID__", "name": "Mother Dairy", "quantity": 2}],
        _session(cart, products),
        message="add mother dairy",
    )
    line = args["cartItems"][0]
    assert (line["productVariantId"], line["storeProductId"], line["quantity"]) == ("P2", "S2", 2)


def test_fabricated_add_is_dropped():
    products = [{"product_variant_id": "P1", "store_product_id": "S1", "name": "Milk"}]
    args = _run(
        [{"productVariantId": "BOGUS", "name": "Quantum Widget", "quantity": 1}],
        _session([], products),
        valid_ids={"P1": "S1"},
    )
    assert args["cartItems"] == []


def test_replace_cart_is_stripped():
    cart = [{"product_variant_id": "P1", "store_product_id": "S1", "name": "Milk"}]
    args = {"replaceCart": True, "cartItems": [{"productVariantId": "__ZEPTO_PVID__", "quantity": 5}]}
    resolve_cart_items(args, _session(cart), valid_ids={}, device_id=_DEV, message="make it 5")
    assert "replaceCart" not in args
    assert args["cartItems"][0]["quantity"] == 5
