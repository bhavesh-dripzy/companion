"""Golden test: the clean `SwiggyFoodProvider` builds correct cards from REAL data.

Drives `SwiggyFoodProvider` directly against sanitized real captures
(fixtures/swiggy_food/{search_pizza,menu_olio}.json from a live read-only run on
admin_bhavesh tokens), asserting the menu + restaurant cards + session LRU reflect
the real restaurant the live turn surfaced (Olio, id 760631).
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.swiggy_food import SwiggyFoodProvider

pytestmark = pytest.mark.component

_DIR = Path(__file__).parent / "fixtures/swiggy_food"
_SEARCH = json.loads((_DIR / "search_pizza.json").read_text())
_MENU = json.loads((_DIR / "menu_olio.json").read_text())


def _result() -> ProviderResult:
    return ProviderResult(
        provider="swiggy_food",
        results=[
            ToolResult(
                tool=_SEARCH["tool"],
                provider="swiggy_food",
                ok=not (_SEARCH["result"] or {}).get("is_error"),
                arguments=_SEARCH.get("arguments") or {},
                data=_SEARCH["result"],
            ),
            ToolResult(
                tool=_MENU["tool"],
                provider="swiggy_food",
                ok=not (_MENU["result"] or {}).get("is_error"),
                data=_MENU["result"],
            ),
        ],
    )


def test_clean_provider_builds_menu_and_restaurant_cards_from_real_data():
    cards = SwiggyFoodProvider().build_attachments(_result())
    kinds = [c.kind for c in cards]
    assert kinds == ["menu_cards", "restaurant_cards"], "menu renders before restaurant"

    menu = next(c for c in cards if c.kind == "menu_cards").to_wire()
    assert menu["provider"] == "swiggy" and str(menu["res_id"]) == "760631"
    assert menu["restaurant_name"] == "Olio - The Wood Fired Pizzeria"
    first = menu["items"][0]
    assert first["name"] == "Fiery Jalapeno & Paprika Royal Pizza Slice"
    assert first["price"] == 199 and first["veg_kind"] == "veg"
    # Swiggy reuses one id for both card keys (frontend reads variant_id then item_id).
    assert first["variant_id"] == first["item_id"]

    resto = next(c for c in cards if c.kind == "restaurant_cards").to_wire()
    assert resto["provider"] == "swiggy" and resto["keyword"] == "pizza"
    blob = json.dumps(resto)
    assert "760631" in blob, "the real restaurant id from the live capture should appear"


def test_clean_provider_builds_session_menu_from_real_menu():
    menu_tr = ToolResult(
        tool=_MENU["tool"],
        provider="swiggy_food",
        ok=not (_MENU["result"] or {}).get("is_error"),
        data=_MENU["result"],
    )
    merged = SwiggyFoodProvider().merge(
        {}, ProviderResult(provider="swiggy_food", results=[menu_tr])
    )
    session_menu = merged.get("session_menu") or []
    assert session_menu, "menu items should be cached for cart-by-name"
    first = session_menu[0]
    assert first["item_name"] == "Fiery Jalapeno & Paprika Royal Pizza Slice"
    assert first["res_id"] == "760631" and first["price"] == 199


def test_clean_provider_builds_cart_card_and_session_cart():
    # get_food_cart / update_food_cart are mutating to reach; drive a representative
    # envelope shaped as _extract_swiggy_cart_snapshot reads it.
    env = {
        "is_error": False,
        "data": {
            "data": {
                "cart_id": 99,
                "restaurantId": "760631",
                "items": [
                    {
                        "menu_item_id": "i1",
                        "name": "Margherita",
                        "quantity": 2,
                        "subtotal": 296,
                        "final_price": 159,
                        "is_veg": 1,
                    }
                ],
                "pricing": {"item_total": 296, "delivery_charge": 30, "to_pay": 209},
                "offers": {"coupon_applied": "TRYNEW", "coupon_discount": 50},
                "restaurant": {"deliverySubtitle": "35-40 mins to Home"},
            },
            "availablePaymentMethods": ["Cash on Delivery"],
        },
    }
    tr = ToolResult(
        tool="update_food_cart",
        provider="swiggy_food",
        ok=True,
        arguments={"restaurantName": "Olio"},
        data=env,
    )
    provider = SwiggyFoodProvider()
    result = ProviderResult(provider="swiggy_food", results=[tr])
    cards = [c for c in provider.build_attachments(result) if c.kind == "cart_card"]
    assert len(cards) == 1
    cart = cards[0].to_wire()
    assert cart["provider"] == "swiggy" and cart["cart"]["restaurant_name"] == "Olio"
    assert (
        cart["cart"]["items"][0]["discounted_price"] == 159
        and cart["cart"]["promo_code"] == "TRYNEW"
    )
    assert provider.merge({}, result)["session_cart"]["cart_id"] == "99"


def test_clean_provider_session_restaurants_round_trip():
    merged = SwiggyFoodProvider().merge({}, _result())
    restaurants = merged.get("session_restaurants") or []
    assert restaurants, "expected session_restaurants carried forward from real data"
    assert restaurants[0]["res_id"] == "760631", "most-recent restaurant should lead the LRU"
    assert all("cuisines" not in r and "res_image" not in r for r in restaurants), "lean projection"
