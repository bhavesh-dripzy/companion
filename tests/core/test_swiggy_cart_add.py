"""Swiggy add-to-cart machinery — regression tests for the 2026-06-10 failure
("Swiggy ka cart update karne mein technical issue aa raha hai").

The clean rewrite dropped the legacy executor's cart machinery: the planner's
mandated search_menu → update_food_cart chain emits __MENU_ITEM_ID__, which the
clean resolver never learned, so every search-first cart add died with
UnresolvedPlaceholderError and the narrator apologised about a "technical
issue". These tests pin the ported pieces: in-plan item-id learning (transient,
never persisted), request-shape normalization, the variant gate (auto-inject
trivial / defer real choices), and session rows carrying variant schemas.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from core.contracts.tool_call import ToolCall
from core.providers.base import ExecCtx
from core.providers.mcp import mcp_base
from core.providers.plugins.swiggy_food.cart_args import (
    is_choice_trivial,
    normalize_cart_payload,
    pick_default_variants,
)
from core.providers.plugins.swiggy_food.menu_cards import menu_entries
from core.providers.plugins.swiggy_food.placeholders import SwiggyFoodResolver
from core.providers.plugins.swiggy_food.provider import SwiggyFoodProvider

pytestmark = pytest.mark.unit

_SIZE_GROUP = {
    "groupId": "g1",
    "name": "Size",
    "variations": [
        {"id": "v8", "name": '8"', "price": 109, "inStock": 1, "default": 1},
        {"id": "v12", "name": '12"', "price": 209, "inStock": 1},
    ],
}
_SINGLE_GROUP = {
    "groupId": "g2",
    "name": "Quantity",
    "variations": [{"id": "v1", "name": "Regular", "inStock": 1}],
}

_SEARCH_MENU_ENVELOPE = {
    "is_error": False,
    "data": {
        "restaurant": {"id": "55619", "name": "Paparizza"},
        "items": [
            {
                "menu_item_id": "MI1",
                "name": "Margherita Pizza",
                "price": 109,
                "hasVariants": True,
                "variantsV2": [_SIZE_GROUP],
            }
        ],
    },
}


def test_resolver_learns_menu_item_id_in_plan_but_never_persists_it():
    r = SwiggyFoodResolver()
    values: dict[str, str] = {}
    learned = r.learn(values, _SEARCH_MENU_ENVELOPE)
    assert values["__MENU_ITEM_ID__"] == "MI1"
    assert learned["__MENU_ITEM_ID__"] == "MI1"
    assert "__MENU_ITEM_ID__" in r.transient and "__ITEM_ID__" in r.transient
    r.clear_stale(values, "search_menu")
    assert "__MENU_ITEM_ID__" not in values, "a fresh search re-learns the item id"


def test_normalize_cart_payload_coerces_response_shape_to_request_shape():
    args = {
        "restaurantId": "55619",
        "cartItems": [
            {
                "itemId": "MI1",
                "quantity": 1,
                "variantsV2": [{"groupId": "g1", "variations": [{"id": "v12"}]}],
                "addons": [{"groupId": "a1", "id": "c1"}],
            }
        ],
    }
    normalize_cart_payload(args)
    item = args["cartItems"][0]
    assert item["menu_item_id"] == "MI1" and "itemId" not in item
    assert item["variantsV2"] == [{"group_id": "g1", "variation_id": "v12"}]
    assert item["addons"] == [{"group_id": "a1", "choice_id": "c1"}]


def test_variant_helpers_trivial_vs_real_choice():
    assert is_choice_trivial([_SINGLE_GROUP])
    assert not is_choice_trivial([_SIZE_GROUP])
    assert pick_default_variants([_SIZE_GROUP]) == [{"group_id": "g1", "variation_id": "v8"}]


def test_before_call_defers_real_variant_choice_via_scratch_index():
    provider = SwiggyFoodProvider()
    ctx = ExecCtx()
    provider.harvest("search_menu", _SEARCH_MENU_ENVELOPE, ctx)
    envelope = provider.before_call(
        "update_food_cart",
        {"restaurantId": "55619", "cartItems": [{"menu_item_id": "MI1", "quantity": 1}]},
        ctx,
    )
    assert envelope is not None, "multi-size item without a pick must not reach the MCP"
    pending = envelope["data"]["variant_choice_required"]
    assert pending[0]["item_name"] == "Margherita Pizza"
    assert [c["id"] for c in pending[0]["groups"][0]["choices"]] == ["v8", "v12"]


def test_before_call_injects_trivial_variant_and_sends():
    provider = SwiggyFoodProvider()
    ctx = ExecCtx()
    trivial = {
        "is_error": False,
        "data": {
            "items": [
                {
                    "menu_item_id": "MI2",
                    "name": "Coke",
                    "hasVariants": True,
                    "variantsV2": [_SINGLE_GROUP],
                }
            ]
        },
    }
    provider.harvest("search_menu", trivial, ctx)
    args = {"restaurantId": "55619", "cartItems": [{"menu_item_id": "MI2", "quantity": 1}]}
    assert provider.before_call("update_food_cart", args, ctx) is None, "call proceeds"
    assert args["cartItems"][0]["variantsV2"] == [{"group_id": "g2", "variation_id": "v1"}]


def test_before_call_falls_back_to_session_menu_rows():
    provider = SwiggyFoodProvider()
    ctx = ExecCtx(
        session={
            "session_menu": [
                {
                    "variant_id": "MI1",
                    "menu_item_id": "MI1",
                    "item_name": "Margherita Pizza",
                    "provider": "swiggy_food",
                    "hasVariants": True,
                    "variantsV2": [_SIZE_GROUP],
                }
            ]
        }
    )
    envelope = provider.before_call(
        "update_food_cart",
        {"restaurantId": "55619", "cartItems": [{"menu_item_id": "MI1", "quantity": 1}]},
        ctx,
    )
    assert envelope is not None
    assert envelope["data"]["variant_choice_required"][0]["item_id"] == "MI1"


def test_menu_entries_carry_cart_ids_and_variant_schema():
    rows = menu_entries(_SEARCH_MENU_ENVELOPE)
    assert rows[0]["menu_item_id"] == "MI1"
    assert rows[0]["hasVariants"] is True
    assert rows[0]["variantsV2"][0]["groupId"] == "g1"
    assert [v["id"] for v in rows[0]["variantsV2"][0]["variations"]] == ["v8", "v12"]


def test_run_chains_search_menu_to_cart_add_in_one_plan(monkeypatch):
    """The 13:06 failure end-to-end: search_menu → update_food_cart with
    __MENU_ITEM_ID__ must substitute the learned id (it used to raise
    UnresolvedPlaceholderError), and the learned item id must NOT be persisted
    to the next turn."""

    @asynccontextmanager
    async def fake_session(spec, init_timeout=20):
        yield "SESSION"

    seen: list[tuple[str, dict]] = []

    async def fake_call_tool(session, name, arguments, timeout=30):
        seen.append((name, dict(arguments)))

        class _Raw:
            isError = False
            structuredContent = (
                _SEARCH_MENU_ENVELOPE["data"] if name == "search_menu" else {"ok": True}
            )
            content = ()

        return _Raw()

    monkeypatch.setattr(mcp_base, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_base, "call_tool", fake_call_tool)

    result = asyncio.run(
        SwiggyFoodProvider().run(
            [
                ToolCall(
                    tool="search_menu",
                    arguments={"query": "margherita", "restaurantIdOfAddedItem": "55619"},
                ),
                ToolCall(
                    tool="update_food_cart",
                    arguments={
                        "restaurantId": "55619",
                        "cartItems": [
                            {
                                "menu_item_id": "__MENU_ITEM_ID__",
                                "quantity": 1,
                                "variantsV2": [{"group_id": "g1", "variation_id": "v12"}],
                            }
                        ],
                    },
                ),
            ],
            ExecCtx(placeholders={"__ADDRESS_ID__": "A1"}),
        )
    )
    assert result.ok, [r.error_detail for r in result.results if not r.ok]
    cart_call = next(args for name, args in seen if name == "update_food_cart")
    assert cart_call["cartItems"][0]["menu_item_id"] == "MI1", "learned id substituted"
    persisted = {k for r in result.results for k in r.learned_placeholders}
    assert "__MENU_ITEM_ID__" not in persisted and "__ITEM_ID__" not in persisted
