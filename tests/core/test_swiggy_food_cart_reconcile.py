"""Swiggy Food cart upsert/reconcile + address anchoring (port of the Instamart/Zepto
robustness, grounded in the live POC: update_food_cart is REPLACE-ALL, the cart
envelope carries per-item variants but no restaurantId)."""

import pytest

from core.providers.base import ExecCtx
from core.providers.plugins.swiggy_food.cart_reconcile import (
    reconcile_cart_items,
    request_rows_from_cart,
    variant_rows_valid,
)
from core.providers.plugins.swiggy_food.provider import (
    _SESSION_SELECTED_ADDRESS,
    SwiggyFoodProvider,
)

pytestmark = pytest.mark.component

# A real get_food_cart envelope shape (trimmed) from the live POC: one item with two
# crust/size variant groups, no restaurantId on the root.
_CART_ENV = {
    "data": {
        "cart_id": 587776294,
        "items": [
            {
                "menu_item_id": 125372544,
                "name": "Margherita",
                "quantity": 2,
                "variants": [
                    {"variation_id": 136721957, "group_id": 42849305, "name": "Pan"},
                    {"variation_id": 136721960, "group_id": 42849306, "name": "Personal"},
                ],
                "addons": [],
            }
        ],
    }
}


def test_request_rows_rebuilds_variants_in_request_shape():
    rows = request_rows_from_cart(_CART_ENV)
    assert rows == [
        {
            "menu_item_id": "125372544",
            "quantity": 2,
            "variantsV2": [
                {"group_id": "42849305", "variation_id": "136721957"},
                {"group_id": "42849306", "variation_id": "136721960"},
            ],
        }
    ]


def test_request_rows_empty_for_null_cart():
    assert request_rows_from_cart({"data": None}) == []
    assert request_rows_from_cart({"is_error": True}) == []


def test_upsert_preserves_existing_line_when_planner_adds_only_new_item():
    # The core REPLACE-ALL bug: planner lists only the new item B; without the fold
    # Swiggy would wipe item A. The reconcile folds A back in.
    existing = request_rows_from_cart(_CART_ENV)
    args = {"cartItems": [{"menu_item_id": "13210740", "quantity": 1}]}
    reconcile_cart_items(args, existing_rows=existing, valid_ids={"13210740"}, fold=True)
    ids = sorted(i["menu_item_id"] for i in args["cartItems"])
    assert ids == ["125372544", "13210740"]
    # The preserved line keeps its variant rows.
    preserved = next(i for i in args["cartItems"] if i["menu_item_id"] == "125372544")
    assert preserved["variantsV2"] and preserved["quantity"] == 2


def test_qty_change_on_existing_line_keeps_its_variants():
    existing = request_rows_from_cart(_CART_ENV)
    args = {"cartItems": [{"menu_item_id": "125372544", "quantity": 5}]}
    reconcile_cart_items(args, existing_rows=existing, valid_ids=set(), fold=True)
    assert args["cartItems"] == [
        {
            "menu_item_id": "125372544",
            "quantity": 5,
            "variantsV2": [
                {"group_id": "42849305", "variation_id": "136721957"},
                {"group_id": "42849306", "variation_id": "136721960"},
            ],
        }
    ]


def test_qty_zero_removes_by_omission():
    existing = request_rows_from_cart(_CART_ENV)
    args = {"cartItems": [{"menu_item_id": "125372544", "quantity": 0}]}
    reconcile_cart_items(args, existing_rows=existing, valid_ids=set(), fold=True)
    assert args["cartItems"] == []  # removed; never sent as qty 0


def test_fabricated_item_id_is_dropped():
    args = {"cartItems": [{"menu_item_id": "999", "quantity": 1}]}
    reconcile_cart_items(args, existing_rows=[], valid_ids={"13210740"}, fold=True)
    assert args["cartItems"] == []


def test_restaurant_switch_does_not_fold_old_cart():
    # fold=False (a different restaurant) → the old item is NOT carried; Swiggy
    # replaces the whole cart with the new restaurant's items.
    existing = request_rows_from_cart(_CART_ENV)
    args = {"cartItems": [{"menu_item_id": "70001", "quantity": 1}]}
    reconcile_cart_items(args, existing_rows=existing, valid_ids={"70001"}, fold=False)
    assert args["cartItems"] == [{"menu_item_id": "70001", "quantity": 1}]


def _ctx(committed=None, saved=None):
    session = {}
    if committed:
        session[_SESSION_SELECTED_ADDRESS] = committed
    if saved is not None:
        session["session_addresses"] = saved
    return ExecCtx(session=session)


def test_ensure_address_overrides_planner_drift_with_committed_anchor():
    p = SwiggyFoodProvider()
    ctx = _ctx(committed="A1")
    args = {"addressId": "__ADDRESS_ID__"}
    p._ensure_address(args, "addressId", ctx)
    assert args["addressId"] == "A1"


def test_ensure_address_honors_explicit_switch_to_saved_address():
    p = SwiggyFoodProvider()
    ctx = _ctx(committed="A1", saved=[{"address_id": "A2", "label": "Work"}])
    args = {"addressId": "A2"}  # planner copied a concrete saved id → a real switch
    p._ensure_address(args, "addressId", ctx)
    assert args["addressId"] == "A2"
    assert ctx.scratch.get("swiggy_food_switched_address_id") == "A2"


def test_ensure_address_ignores_fabricated_id_and_keeps_anchor():
    p = SwiggyFoodProvider()
    ctx = _ctx(committed="A1", saved=[{"address_id": "A2"}])
    args = {"addressId": "FAKE"}  # not a saved id → fabrication, anchor wins
    p._ensure_address(args, "addressId", ctx)
    assert args["addressId"] == "A1"
    assert "swiggy_food_switched_address_id" not in ctx.scratch


def test_ensure_restaurant_backfills_id_and_name_on_cart_edit():
    # The planner omits restaurantId on a follow-up edit; Swiggy REQUIRES it. We
    # backfill from the cart (res_id) and the searched-restaurants LRU (name).
    p = SwiggyFoodProvider()
    ctx = ExecCtx(
        session={
            "session_cart": {"res_id": "53777", "items": []},
            "session_restaurants": [{"res_id": "53777", "name": "Pizza Hut"}],
        }
    )
    args = {"cartItems": [{"menu_item_id": "1", "quantity": 2}]}  # no restaurantId
    p._ensure_restaurant(args, ctx)
    assert args["restaurantId"] == "53777"
    assert args["restaurantName"] == "Pizza Hut"


def test_ensure_restaurant_keeps_explicit_switch():
    p = SwiggyFoodProvider()
    ctx = ExecCtx(session={"session_cart": {"res_id": "53777"}})
    args = {"restaurantId": "999", "cartItems": [{"menu_item_id": "x", "quantity": 1}]}
    p._ensure_restaurant(args, ctx)
    assert args["restaurantId"] == "999"  # a real switch to another restaurant wins


# Real menu variantsV2 (response shape) for the Margherita from the live POC.
_REAL_V2 = [
    {"groupId": "42849305", "variations": [{"id": "136721957", "inStock": 1, "default": 1}]},
    {"groupId": "42849306", "variations": [{"id": "136721960", "inStock": 1, "default": 1}]},
]


def test_variant_rows_valid_accepts_correct_rows():
    rows = [
        {"group_id": "42849305", "variation_id": "136721957"},
        {"group_id": "42849306", "variation_id": "136721960"},
    ]
    assert variant_rows_valid(rows, _REAL_V2) is True


def test_variant_rows_valid_rejects_field_swap():
    # Sarvam's mistake: group NAME in group_id, a GROUP id in variation_id.
    bad = [{"group_id": "Crust", "variation_id": "42849305"}]
    assert variant_rows_valid(bad, _REAL_V2) is False


def test_variant_rows_valid_no_menu_does_not_second_guess():
    assert variant_rows_valid([{"group_id": "x", "variation_id": "y"}], []) is True


def _ctx_with_menu(iid):
    ctx = ExecCtx()
    ctx.scratch["swiggy_menu_index"] = {iid: {"hasVariants": True, "variantsV2": _REAL_V2}}
    return ctx


def test_repair_variants_leaves_valid_rows_untouched():
    p = SwiggyFoodProvider()
    ctx = _ctx_with_menu("125372544")
    item = {
        "menu_item_id": "125372544",
        "variantsV2": [
            {"group_id": "42849305", "variation_id": "136721957"},
            {"group_id": "42849306", "variation_id": "136721960"},
        ],
    }
    before = [dict(r) for r in item["variantsV2"]]
    p._repair_variants(item, "125372544", ctx)
    assert item["variantsV2"] == before  # correct rows are never altered


def test_repair_variants_fixes_malformed_rows_to_valid_defaults():
    p = SwiggyFoodProvider()
    ctx = _ctx_with_menu("125372544")
    item = {"menu_item_id": "125372544", "variantsV2": [{"group_id": "Crust", "variation_id": "42849305"}]}
    p._repair_variants(item, "125372544", ctx)
    # repaired to real, valid {group_id, variation_id} pairs from the menu
    assert variant_rows_valid(item["variantsV2"], _REAL_V2) is True


def test_repair_variants_noop_without_menu():
    p = SwiggyFoodProvider()
    item = {"menu_item_id": "999", "variantsV2": [{"group_id": "Crust", "variation_id": "x"}]}
    p._repair_variants(item, "999", ExecCtx())  # no menu index → left as-is
    assert item["variantsV2"] == [{"group_id": "Crust", "variation_id": "x"}]


def test_variant_gate_skips_item_already_in_cart():
    # A bare quantity change on an item already in the cart must NOT re-prompt for
    # crust/size — it keeps its chosen variant through the upsert fold.
    p = SwiggyFoodProvider()
    ctx = ExecCtx(session={"session_cart": {"items": [{"menu_item_id": "125372544"}]}})
    # menu index says this item has variants, but it's already in the cart.
    ctx.scratch["swiggy_menu_index"] = {
        "125372544": {"name": "Margherita", "hasVariants": True, "variantsV2": [{"groupId": "g"}]}
    }
    args = {"cartItems": [{"menu_item_id": "125372544", "quantity": 2}]}
    assert p._variant_gate(args, ctx) is None  # not deferred
