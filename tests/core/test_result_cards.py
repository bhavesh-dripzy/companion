"""Long-tail result cards — order history / tracking / coupons / slots /
reviews / kite tables / generic info_card fallback.

Order extractors are validated against REAL envelopes harvested live on
2026-06-10 (tests/core/fixtures/orders/): Zomato's nested
`order_history_items[]` and Zepto's `orders[]` with paise amounts.
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ToolResult
from core.providers.mcp.coupon_cards import coupon_entries
from core.providers.mcp.info_cards import build_info_cards, info_card_payload
from core.providers.mcp.result_cards import (
    build_order_cards,
    order_entries,
    tracking_snapshot,
)
from core.providers.plugins.district.slots_reviews import review_entries, slot_entries

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).parent / "fixtures" / "orders"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def test_zomato_order_history_entries_from_real_envelope():
    entries = order_entries(_load("zomato.get_order_history.json"))
    assert len(entries) == 1
    order = entries[0]
    assert order["order_id"] == "6939141894"
    assert order["status"] == "Delivered"
    assert order["place_name"] == "California Burrito"
    assert order["total_text"] == "₹270.00"
    assert order["date"] == "02 Jun, 11:37PM"
    assert order["items"][0] == {"name": "Barbeque Paneer Rice Bowl (Regular)", "quantity": 1}


def test_zepto_order_history_entries_convert_paise():
    entries = order_entries(_load("zepto.list_order_history.json"), paise=True)
    assert entries, "real zepto envelope must yield orders"
    first = entries[0]
    assert first["status"] == "DELIVERED"
    assert first["total"] == 170.0, "grandTotalAmount is paise"
    assert any("Ice Cream" in i["name"] for i in first["items"])


def test_order_cards_tagged_with_brand_and_source():
    tr = ToolResult(
        tool="get_order_history",
        provider="zomato",
        ok=True,
        data=_load("zomato.get_order_history.json"),
    )
    cards = build_order_cards(
        [tr], tools={"get_order_history"}, provider_id="zomato", brand="zomato"
    )
    assert cards[0].kind == "order_cards"
    wire = cards[0].to_wire()
    assert wire["provider"] == "zomato" and wire["source"] == "zomato.get_order_history"


def test_tracking_snapshot_empty_and_live():
    idle = {"is_error": False, "data": {"result": {"order_tracking": {"order_tracking_items": []}}}}
    assert tracking_snapshot(idle) is None
    live = {
        "is_error": False,
        "data": {
            "order_status": "OUT_FOR_DELIVERY",
            "eta": "12 mins",
            "order_id": "OD9",
            "res_name": "Gianis",
        },
    }
    snap = tracking_snapshot(live)
    assert snap == {
        "status": "OUT_FOR_DELIVERY",
        "eta": "12 mins",
        "order_id": "OD9",
        "place_name": "Gianis",
    }


def test_coupon_entries_dedup_and_cap():
    envelope = {
        "is_error": False,
        "data": {
            "coupons": [
                {"code": "TRYNEW", "header": "60% OFF up to ₹120", "description": "New users"},
                {"code": "TRYNEW", "header": "dup ignored"},
                {"title": "Free delivery", "discount_text": "₹0 delivery fee"},
            ]
        },
    }
    entries = coupon_entries(envelope)
    assert len(entries) == 2
    assert entries[0] == {
        "code": "TRYNEW",
        "title": "60% OFF up to ₹120",
        "description": "New users",
    }
    assert entries[1]["savings_text"] == "₹0 delivery fee"


def test_slot_and_review_entries():
    slots = slot_entries(
        {
            "is_error": False,
            "data": {"slots": [{"slot_time": "7:00 PM", "available": True}, {"time": "9:30 PM"}]},
        }
    )
    assert [s["time"] for s in slots] == ["7:00 PM", "9:30 PM"]
    assert slots[0]["available"] is True

    reviews = review_entries(
        {
            "is_error": False,
            "data": {
                "reviews": [
                    {"critic_name": "A. Critic", "rating": 4, "review": "Tight, kinetic thriller."}
                ]
            },
        }
    )
    assert reviews[0]["critic"] == "A. Critic" and reviews[0]["rating"] == "4"


def test_kite_table_rows():
    from core.providers.plugins.kite.cards import build_attachments

    results = [
        ToolResult(
            tool="get_gtts",
            provider="kite",
            ok=True,
            data={
                "data": {
                    "gtts": [
                        {
                            "id": 11,
                            "status": "active",
                            "type": "single",
                            "condition": {"tradingsymbol": "INFY", "trigger_values": [1500]},
                            "orders": [{"transaction_type": "SELL", "quantity": 5, "price": 1501}],
                        }
                    ]
                }
            },
        ),
        ToolResult(
            tool="get_trades",
            provider="kite",
            ok=True,
            data={
                "data": [
                    {
                        "tradingsymbol": "TCS",
                        "transaction_type": "BUY",
                        "quantity": 2,
                        "average_price": 4100.5,
                        "order_id": "O1",
                    }
                ]
            },
        ),
        ToolResult(
            tool="get_mf_holdings",
            provider="kite",
            ok=True,
            data={"data": [{"fund": "Parag Parikh Flexi Cap", "quantity": 103.2, "pnl": 5200}]},
        ),
    ]
    kinds = {a.kind: a.to_wire() for a in build_attachments(results)}
    assert kinds["kite_gtt_card"]["gtts"][0]["tradingsymbol"] == "INFY"
    assert kinds["kite_trades_card"]["trades"][0]["average_price"] == 4100.5
    assert kinds["kite_mf_holdings_card"]["mf_holdings"][0]["fund"] == "Parag Parikh Flexi Cap"


def test_info_card_fallback_covers_uncovered_tools_only():
    results = [
        ToolResult(
            tool="get_location_serviceability",
            provider="zepto",
            ok=True,
            data={"data": {"serviceable": True, "store_name": "Zepto Now DLF"}},
        ),
        ToolResult(
            tool="search_products",
            provider="zepto",
            ok=True,
            data={"data": {"products": [{"name": "x"}]}},
        ),
    ]
    cards = build_info_cards(
        results, exclude={"search_products"}, provider_id="zepto", brand="zepto"
    )
    assert len(cards) == 1
    wire = cards[0].to_wire()
    assert wire["kind"] == "info_card"
    assert wire["info"]["title"] == "Get Location Serviceability"
    labels = {r["label"]: r["value"] for r in wire["info"]["rows"]}
    assert labels == {"Serviceable": "True", "Store Name": "Zepto Now DLF"}


def test_info_card_payload_none_for_empty():
    assert info_card_payload("get_orders", {"is_error": False, "data": {}}) is None
    assert info_card_payload("get_orders", {"is_error": True, "data": {"x": 1}}) is None
