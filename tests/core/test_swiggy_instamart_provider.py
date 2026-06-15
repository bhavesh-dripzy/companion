"""Golden test: the clean `SwiggyInstamartProvider` builds product cards from REAL data.

Drives the provider against a sanitized real capture (search "milk", live read-only
run on admin_bhavesh), asserting the per-variant product cards + spinId LRU reflect
the real product the live turn surfaced (Amul Gold Milk 1 Ltr, spinId S8PF9T8YLG).
"""

import asyncio
import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.base import ExecCtx
from core.providers.mcp.mcp_base import McpProvider
from core.providers.plugins.swiggy_instamart import SwiggyInstamartProvider

pytestmark = pytest.mark.component


def test_instamart_address_auto_resolution_configured():
    assert SwiggyInstamartProvider.saved_addresses_tool == "get_addresses"
    assert SwiggyInstamartProvider.saved_addresses_session_key == "session_instamart_addresses"


def test_instamart_merge_surfaces_instamart_addresses():
    addr = ToolResult(
        tool="get_addresses",
        provider="swiggy_instamart",
        ok=True,
        data={"data": {"addresses": [{"addressId": "IM1", "addressTag": "Home"}]}},
    )
    merged = SwiggyInstamartProvider().merge(
        {}, ProviderResult(provider="swiggy_instamart", results=[addr])
    )
    rows = merged.get("session_instamart_addresses") or []
    assert rows and rows[0]["address_id"] == "IM1"
    assert "session_addresses" not in merged


def test_instamart_before_run_resolves_from_carried_instamart_addresses():
    ctx = ExecCtx(session={"session_instamart_addresses": [{"address_id": "IM9", "label": "Home"}]})
    asyncio.run(SwiggyInstamartProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "IM9"


def test_instamart_before_run_auto_fetch_resolves_id_only_rows(monkeypatch):
    """Instamart rows key on `id`; the auto-fetch must resolve __ADDRESS_ID__ from it."""
    envelope = {"data": {"addresses": [{"id": "363294542", "addressTag": "Home"}]}}

    async def fake_call_tool(session, name, arguments=None):
        assert name == "get_addresses"
        return "RAW"

    monkeypatch.setattr("core.providers.mcp.mcp_base.call_tool", fake_call_tool)
    monkeypatch.setattr("core.providers.mcp.mcp_base.tool_result_payload", lambda raw: envelope)
    ctx = ExecCtx()
    asyncio.run(SwiggyInstamartProvider().before_run(session=object(), ctx=ctx))
    assert ctx.placeholders.get("__ADDRESS_ID__") == "363294542"


def test_instamart_before_run_records_resolved_address_in_scratch(monkeypatch):
    """before_run must record the resolved id + full list into ctx.scratch so the
    UI can show 'Delivering to ...' even when search args omit addressId."""
    envelope = {
        "structured": {
            "addresses": [
                {"id": "360938440", "addressTag": "Room", "addressLine": "Bhavesh: Crown Plaza"},
                {"id": "275412200", "addressTag": "Work", "addressLine": "Bhavesh: H1 Hostel"},
            ]
        }
    }

    async def fake_call_tool(session, name, arguments=None):
        return "RAW"

    monkeypatch.setattr("core.providers.mcp.mcp_base.call_tool", fake_call_tool)
    monkeypatch.setattr("core.providers.mcp.mcp_base.tool_result_payload", lambda raw: envelope)
    ctx = ExecCtx()
    asyncio.run(SwiggyInstamartProvider().before_run(session=object(), ctx=ctx))
    assert ctx.scratch["resolved_address_id"] == "360938440"
    assert [r["address_id"] for r in ctx.scratch["saved_address_rows"]] == ["360938440", "275412200"]


def test_instamart_search_without_addressid_arg_still_shows_delivery_address():
    """The real bug: search_products args carry no addressId, yet the product-cards
    attachment must still expose the delivery address (from resolved scratch)."""
    search = ToolResult(
        tool=_FIX["tool"],
        provider="swiggy_instamart",
        ok=True,
        arguments={"query": "milk"},  # no addressId — the real captured shape
        data=_FIX["result"],
    )
    result = ProviderResult(provider="swiggy_instamart", results=[search])
    result.diagnostics["resolved_address_id"] = "360938440"
    result.diagnostics["saved_address_rows"] = [
        {"address_id": "360938440", "addressTag": "Room", "addressLine": "Bhavesh: Crown Plaza"},
        {"address_id": "275412200", "addressTag": "Work", "addressLine": "Bhavesh: H1 Hostel"},
    ]
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(result)
        if c.kind == "instamart_product_cards"
    ]
    assert cards, "expected a product-cards attachment"
    wire = cards[0].to_wire()
    assert wire["delivery_address_id"] == "360938440"
    addrs = wire["delivery_addresses"]
    assert {a["tag"] for a in addrs} == {"Room", "Work"}
    assert addrs[0]["id"] == "360938440"


def test_instamart_display_address_follows_the_address_search_used(monkeypatch):
    """When the user switches address, the search runs with that concrete addressId
    (a required arg). Display + persistence must follow it, NOT before_run's nearest
    pick. Regression: switching address left the bar showing the original address."""
    rows = [
        {"id": "HOLIDAY", "addressTag": "Holiday", "addressLine": "Holiday Inn Gurgaon"},
        {"id": "ROOM", "addressTag": "Room", "addressLine": "Crown Plaza"},
    ]

    async def fake_run(self, calls, ctx):
        # before_run resolved nearest (Holiday) into scratch; the search itself
        # ran against ROOM (planner copied the picker's addressId).
        ctx.scratch["saved_address_rows"] = rows
        ctx.scratch["resolved_address_id"] = "HOLIDAY"
        search = ToolResult(
            tool="search_products",
            provider="swiggy_instamart",
            ok=True,
            arguments={"query": "soda", "addressId": "ROOM"},
            data=_FIX["result"],
        )
        return ProviderResult(provider="swiggy_instamart", results=[search])

    monkeypatch.setattr(McpProvider, "run", fake_run)
    prov = SwiggyInstamartProvider()
    result = asyncio.run(prov.run([], ExecCtx()))
    assert result.diagnostics["resolved_address_id"] == "ROOM"

    cards = [c for c in prov.build_attachments(result) if c.kind == "instamart_product_cards"]
    assert cards[0].to_wire()["delivery_address_id"] == "ROOM"
    # A bare search must NOT set selected_instamart_address_id — that was the drift
    # source (the planner's per-call addressId poisoned the committed address).
    assert not prov.merge({}, result).get("selected_instamart_address_id")


def test_selected_address_anchored_to_cart_not_search():
    """selected_instamart_address_id is driven by the CART's committed address,
    and the cart's address is authoritative for resolution (repairs a stale
    selected that drifted to a different/non-serviceable address)."""
    prov = SwiggyInstamartProvider()
    # A cart on STARRRS must set selected=STARRRS in merge...
    cart_tr = ToolResult(
        tool="get_cart",
        provider="swiggy_instamart",
        ok=True,
        data={
            "data": {
                "selectedAddressDetails": {"id": "STARRRS"},
                "cartTotalAmount": "₹72",
                "items": [{"spinId": "S1", "itemName": "Milk", "quantity": 1, "discountedFinalPrice": 72}],
            }
        },
    )
    merged = prov.merge(
        {"selected_instamart_address_id": "HOLIDAY"},  # stale/poisoned value
        ProviderResult(provider="swiggy_instamart", results=[cart_tr]),
    )
    assert merged["selected_instamart_address_id"] == "STARRRS"  # repaired from cart

    # ...and the live cart's address wins over a stale selected when resolving.
    ctx = ExecCtx(
        session={
            "selected_instamart_address_id": "HOLIDAY",
            "session_instamart_cart": {"items": [{"spin_id": "S1", "quantity": 1}], "selected_address_id": "STARRRS"},
        }
    )
    assert SwiggyInstamartProvider._anchor_address(ctx) == "STARRRS"
    args = {"items": [{"spinId": "S1", "quantity": 2}]}
    prov.before_call("update_cart", args, ctx)
    assert args["selectedAddressId"] == "STARRRS"  # not the stale HOLIDAY


def test_instamart_merge_persists_resolved_addresses_for_next_turn():
    """merge must carry the resolved address list into the session so later turns
    skip the get_addresses round-trip."""
    result = ProviderResult(provider="swiggy_instamart", results=[])
    result.diagnostics["saved_address_rows"] = [
        {"address_id": "360938440", "addressTag": "Room", "label": "Room"}
    ]
    merged = SwiggyInstamartProvider().merge({}, result)
    rows = merged["session_instamart_addresses"]
    assert rows[0]["address_id"] == "360938440" and rows[0]["provider"] == "swiggy_instamart"


_FIX = json.loads(
    (Path(__file__).parent / "fixtures/swiggy_instamart/search_milk.json").read_text()
)


def _result() -> ProviderResult:
    return ProviderResult(
        provider="swiggy_instamart",
        results=[
            ToolResult(
                tool=_FIX["tool"],
                provider="swiggy_instamart",
                ok=not (_FIX["result"] or {}).get("is_error"),
                arguments=_FIX.get("arguments") or {},
                data=_FIX["result"],
            )
        ],
    )


def test_builds_per_variant_product_cards_from_real_data():
    cards = SwiggyInstamartProvider().build_attachments(_result())
    assert len(cards) == 1
    wire = cards[0].to_wire()
    assert wire["kind"] == "instamart_product_cards" and wire["provider"] == "swiggy_instamart"
    products = wire["products"]
    assert products, "expected ≥1 product card"
    blob = json.dumps(products)
    assert "S8PF9T8YLG" in blob, "the real spinId from the live capture should appear"
    # Product-level rows carry `variants: [{spin_id, price_inr, ...}]` — the
    # live capture's Amul Gold Milk has one variation (spinId S8PF9T8YLG).
    amul = next(p for p in products if p["name"] == "Amul Gold Milk 1 Ltr")
    assert amul["brand"] == "Amul" and amul["query"] == "milk"
    spin = next(v for v in amul["variants"] if v["spin_id"] == "S8PF9T8YLG")
    assert spin["price_inr"] == 72.0 and spin["mrp_inr"] == 72.0
    assert spin["in_stock"] is True


def test_builds_cart_card_and_session_cart():
    # get_cart is mutating-state to populate; drive a representative envelope.
    env = {
        "is_error": False,
        "data": {
            "items": [
                {
                    "spinId": "S8PF9T8YLG",
                    "displayName": "Amul Gold Milk 1 Ltr",
                    "quantity": 2,
                    "price": {"mrp": 72, "offerPrice": 72},
                    "imageUrl": "https://x/milk.jpg",
                    "quantityDescription": "1 L",
                }
            ],
            "grandTotalPaise": 14400,
        },
    }
    tr = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=env)
    provider = SwiggyInstamartProvider()
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    cards = [c for c in provider.build_attachments(result) if c.kind == "instamart_cart_card"]
    assert len(cards) == 1
    cart = cards[0].to_wire()["instamart_cart"]
    assert cart["items"][0]["name"] == "Amul Gold Milk 1 Ltr" and cart["items"][0]["quantity"] == 2
    assert cart["grand_total_paise"] == 14400
    assert provider.merge({}, result)["session_instamart_cart"]["items"]


def _exec_ctx_with_cart(items: list[dict]) -> ExecCtx:
    return ExecCtx(session={"session_instamart_cart": {"items": items}})


def test_update_cart_upsert_preserves_existing_lines_when_adding():
    """Regression: adding a second item must NOT wipe the first. The planner sends
    only the new line; the executor folds it onto the existing cart."""
    ctx = _exec_ctx_with_cart([{"spin_id": "ATTA", "quantity": 1}])
    args = {"selectedAddressId": "A1", "items": [{"spinId": "BREAD", "quantity": 1}]}
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"ATTA": 1, "BREAD": 1}


def test_update_cart_upsert_changes_quantity_of_existing_line():
    ctx = _exec_ctx_with_cart([{"spin_id": "ATTA", "quantity": 1}, {"spin_id": "BREAD", "quantity": 1}])
    args = {"items": [{"spinId": "ATTA", "quantity": 3}]}
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"ATTA": 3, "BREAD": 1}


def test_update_cart_upsert_removes_line_on_zero_quantity():
    ctx = _exec_ctx_with_cart([{"spin_id": "ATTA", "quantity": 1}, {"spin_id": "BREAD", "quantity": 2}])
    args = {"items": [{"spinId": "ATTA", "quantity": 0}]}
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"BREAD": 2}


def test_update_cart_after_clear_cart_rebuilds_from_scratch():
    """clear_cart earlier in the plan means update_cart must NOT merge the stale
    session cart back in."""
    ctx = _exec_ctx_with_cart([{"spin_id": "OLD", "quantity": 5}])
    provider = SwiggyInstamartProvider()
    provider.harvest("clear_cart", {"is_error": False}, ctx)
    args = {"items": [{"spinId": "NEW", "quantity": 1}]}
    provider.before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"NEW": 1}


def test_update_cart_injects_address_when_planner_omits_it():
    """Regression: planner omitted the required selectedAddressId → Swiggy errored
    'delivery address is missing'. The executor must inject the resolved address."""
    ctx = ExecCtx(placeholders={"__ADDRESS_ID__": "STARRRS"})
    args = {"items": [{"spinId": "ATTA", "quantity": 1}]}  # no selectedAddressId
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    assert args["selectedAddressId"] == "STARRRS"


def test_search_injects_address_from_session_selected_when_missing():
    ctx = ExecCtx(session={"selected_instamart_address_id": "ROOM"})
    args = {"query": "atta"}  # no addressId
    SwiggyInstamartProvider().before_call("search_products", args, ctx)
    assert args["addressId"] == "ROOM"


def test_address_injection_does_not_override_explicit_choice():
    """A switch flow passes a concrete addressId — it must win over the resolved one."""
    ctx = ExecCtx(placeholders={"__ADDRESS_ID__": "STARRRS"})
    args = {"query": "atta", "addressId": "ROOM"}
    SwiggyInstamartProvider().before_call("search_products", args, ctx)
    assert args["addressId"] == "ROOM"


def test_checkout_injects_address_when_missing():
    ctx = ExecCtx(scratch={"resolved_address_id": "STARRRS"})
    args = {"paymentMethod": "Cash"}
    SwiggyInstamartProvider().before_call("checkout", args, ctx)
    assert args["addressId"] == "STARRRS"


def _live_cart_env() -> dict:
    """The real update_cart / get_cart envelope shape (from a live probe): items
    use itemName / mrp / discountedFinalPrice, total is a rupee string, no image."""
    return {
        "is_error": False,
        "data": {
            "selectedAddressDetails": {"id": "ADDR1", "annotation": "Starrrs"},
            "cartTotalAmount": "₹798",
            "billBreakdown": {"toPay": {"label": "To Pay", "value": "₹798"}},
            "items": [
                {
                    "spinId": "DNF3XGDN9H",
                    "itemName": "Sprite Soft Drink Bottle 12 Pieces",
                    "quantity": 2,
                    "isInStockAndAvailable": True,
                    "mrp": 480,
                    "discountedFinalPrice": 395,
                }
            ],
        },
    }


def test_cart_card_parses_live_shape_and_enriches_image_from_lru():
    env = _live_cart_env()
    tr = ToolResult(tool="update_cart", provider="swiggy_instamart", ok=True, data=env)
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    # LRU (carried from a prior search) supplies the image the cart lacks.
    result.diagnostics["session_instamart_products"] = [
        {
            "name": "Sprite Soft Drink Bottle",
            "brand": "Sprite",
            "variants": [
                {"spin_id": "DNF3XGDN9H", "image_url": "https://img/sprite.png", "quantity_description": "750 ml"}
            ],
        }
    ]
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(result)
        if c.kind == "instamart_cart_card"
    ]
    assert len(cards) == 1
    item = cards[0].to_wire()["instamart_cart"]["items"][0]
    assert item["name"] == "Sprite Soft Drink Bottle 12 Pieces"
    assert item["price_inr"] == 395.0 and item["mrp_inr"] == 480.0
    assert item["image_url"] == "https://img/sprite.png"  # enriched from LRU
    assert cards[0].to_wire()["instamart_cart"]["grand_total_paise"] == 79800


def test_cart_card_surfaces_itemized_bill_breakdown():
    env = {
        "is_error": False,
        "data": {
            "cartTotalAmount": "₹486",
            "items": [{"spinId": "X", "itemName": "Atta", "quantity": 1, "mrp": 524, "discountedFinalPrice": 478}],
            "billBreakdown": {
                "lineItems": [
                    {"label": "Item Total", "value": "₹478.00"},
                    {"label": "Handling Fee", "value": "₹8.00"},
                    {"label": "Delivery Partner Fee", "value": "FREE"},
                ],
                "toPay": {"label": "To Pay", "value": "₹486"},
            },
        },
    }
    tr = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=env)
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(
            ProviderResult(provider="swiggy_instamart", results=[tr])
        )
        if c.kind == "instamart_cart_card"
    ]
    bill = cards[0].to_wire()["instamart_cart"]["bill_breakdown"]
    assert [b["label"] for b in bill] == ["Item Total", "Handling Fee", "Delivery Partner Fee"]
    handling = next(b for b in bill if b["label"] == "Handling Fee")
    assert handling["amount_paise"] == 800 and handling["is_free"] is False
    free = next(b for b in bill if b["label"] == "Delivery Partner Fee")
    assert free["is_free"] is True and free["amount_paise"] is None


def test_cart_cards_dedupe_update_then_get_to_single_final_card():
    """update_cart replaces the whole cart, so update_cart + get_cart in one turn
    describe the same cart — render exactly one card (no duplicates)."""
    upd = ToolResult(tool="update_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env())
    get = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env())
    result = ProviderResult(provider="swiggy_instamart", results=[upd, get])
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(result)
        if c.kind == "instamart_cart_card"
    ]
    assert len(cards) == 1
    assert cards[0].to_wire()["source"] == "swiggy_instamart.get_cart"  # last wins


def test_session_products_lru_keyed_by_product_id():
    merged = SwiggyInstamartProvider().merge({}, _result())
    products = merged.get("session_instamart_products") or []
    assert products, "expected session_instamart_products carried forward"
    # LRU is deduped by product_id; each product row carries its full variants list.
    product_ids = [p["product_id"] for p in products]
    assert "LLB1MIIY7S" in product_ids and len(product_ids) == len(set(product_ids)), (
        "deduped productId LRU"
    )
    # The Amul Gold Milk row (productId LLB1MIIY7S) keeps its single variation.
    amul = next(p for p in products if p["product_id"] == "LLB1MIIY7S")
    spin_ids = [v["spin_id"] for v in amul["variants"]]
    assert "S8PF9T8YLG" in spin_ids
