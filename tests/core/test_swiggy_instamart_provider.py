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
from core.providers.plugins.swiggy_instamart import SwiggyInstamartProvider

pytestmark = pytest.mark.component


def test_instamart_address_auto_resolution_configured():
    # search_products REQUIRES __ADDRESS_ID__; opt in to base auto-resolution
    # under the Instamart-specific session namespace.
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
    assert "session_addresses" not in merged  # must use the Instamart namespace


def test_instamart_before_run_resolves_from_carried_instamart_addresses():
    ctx = ExecCtx(session={"session_instamart_addresses": [{"address_id": "IM9", "label": "Home"}]})
    asyncio.run(SwiggyInstamartProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "IM9"

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
    amul = next(p for p in products if p["spin_id"] == "S8PF9T8YLG")
    assert amul["name"] == "Amul Gold Milk 1 Ltr" and amul["price_inr"] == 72.0
    assert amul["query"] == "milk"


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


def test_session_products_lru_keyed_by_spin_id():
    merged = SwiggyInstamartProvider().merge({}, _result())
    products = merged.get("session_instamart_products") or []
    assert products, "expected session_instamart_products carried forward"
    ids = [p["spin_id"] for p in products]
    assert "S8PF9T8YLG" in ids and len(ids) == len(set(ids)), "deduped spinId LRU"
