"""Golden test: the clean `SwiggyFoodProvider` builds correct cards from REAL data.

Drives `SwiggyFoodProvider` directly against sanitized real captures
(fixtures/swiggy_food/{search_pizza,menu_olio}.json from a live read-only run on
admin_bhavesh tokens), asserting the menu + restaurant cards + session LRU reflect
the real restaurant the live turn surfaced (Olio, id 760631).
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.base import ExecCtx
from core.providers.mcp import mcp_base
from core.providers.plugins.swiggy_food import SwiggyFoodProvider
from core.providers.plugins.swiggy_food.placeholders import SwiggyFoodResolver

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


def test_saved_addresses_tool_enables_auto_resolution():
    assert SwiggyFoodProvider.saved_addresses_tool == "get_addresses"


def test_merge_surfaces_session_addresses():
    addr = ToolResult(
        tool="get_addresses",
        provider="swiggy_food",
        ok=True,
        data={"data": {"addresses": [{"addressId": "SW1", "addressTag": "Home"}]}},
    )
    merged = SwiggyFoodProvider().merge({}, ProviderResult(provider="swiggy_food", results=[addr]))
    rows = merged.get("session_addresses") or []
    assert rows and rows[0]["address_id"] == "SW1"


def test_before_run_resolves_address_from_carried_session():
    ctx = ExecCtx(session={"session_addresses": [{"address_id": "SW9", "label": "Home"}]})
    asyncio.run(SwiggyFoodProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "SW9"


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


def test_resolver_learns_restaurant_id_from_real_search_envelope():
    # Swiggy keys hits as data.restaurants[].id — no alias key — so the shared
    # alias walk alone never resolves __RESTAURANT_ID__ for in-plan chaining.
    values: dict[str, str] = {}
    learned = SwiggyFoodResolver().learn(values, _SEARCH["result"])
    assert values["__RESTAURANT_ID__"] == "760631"
    assert learned["__RESTAURANT_ID__"] == "760631"


def test_resolver_skips_error_envelope():
    values: dict[str, str] = {}
    SwiggyFoodResolver().learn(values, {"is_error": True, "data": {"restaurants": [{"id": 1}]}})
    assert "__RESTAURANT_ID__" not in values


def test_resolver_search_clears_carried_restaurant_id():
    values = {"__RESTAURANT_ID__": "stale-dominos", "__ADDRESS_ID__": "keep"}
    SwiggyFoodResolver().clear_stale(values, "search_restaurants")
    assert "__RESTAURANT_ID__" not in values
    assert values["__ADDRESS_ID__"] == "keep"


def test_resolver_literal_restaurant_arg_pivots_session():
    r = SwiggyFoodResolver()
    values = {"__RESTAURANT_ID__": "111"}
    r.learn_from_args(values, "get_restaurant_menu", {"restaurantId": "222"})
    assert values["__RESTAURANT_ID__"] == "222"
    r.learn_from_args(values, "search_menu", {"restaurantIdOfAddedItem": "__RESTAURANT_ID__"})
    assert values["__RESTAURANT_ID__"] == "222", "placeholder args must not overwrite"
    r.learn_from_args(values, "get_addresses", {"restaurantId": "333"})
    assert values["__RESTAURANT_ID__"] == "222", "non-restaurant-scoped tools must not pivot"


def test_run_chains_search_to_menu_in_one_plan(monkeypatch):
    # Regression: "show me the menu for <named restaurant>" plans
    # search_restaurants → get_restaurant_menu(__RESTAURANT_ID__); the menu step
    # used to die with UnresolvedPlaceholderError because the search response's
    # restaurants[].id was never learned.
    @asynccontextmanager
    async def fake_session(spec, init_timeout=20):
        yield "SESSION"

    seen: list[tuple[str, dict]] = []

    async def fake_call_tool(session, name, arguments, timeout=30):
        seen.append((name, dict(arguments)))

        class _Raw:
            isError = False
            structuredContent = (
                _SEARCH["result"]["data"] if name == "search_restaurants" else {"ok": True}
            )
            content = ()

        return _Raw()

    monkeypatch.setattr(mcp_base, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_base, "call_tool", fake_call_tool)

    result = asyncio.run(
        SwiggyFoodProvider().run(
            [
                ToolCall(tool="search_restaurants", arguments={"query": "Olio"}),
                ToolCall(
                    tool="get_restaurant_menu", arguments={"restaurantId": "__RESTAURANT_ID__"}
                ),
            ],
            ExecCtx(placeholders={"__ADDRESS_ID__": "A1"}),
        )
    )
    assert result.ok, [r.error_detail for r in result.results if not r.ok]
    assert seen[1] == ("get_restaurant_menu", {"restaurantId": "760631"})
