"""Tests for ONDC retail payload builders + on_search catalog parsing."""

from __future__ import annotations

from companion_api.ondc_catalog import parse_catalog
from companion_api.ondc_retail import search_intent, select_message


def test_search_intent_by_name_with_delivery_and_finder_fee():
    msg = search_intent("milk", gps="12.97,77.59", area_code="560001", finder_fee_pct="2.5")
    intent = msg["intent"]
    assert intent["item"]["descriptor"]["name"] == "milk"
    assert intent["fulfillment"]["type"] == "Delivery"
    assert intent["fulfillment"]["end"]["location"]["gps"] == "12.97,77.59"
    assert intent["fulfillment"]["end"]["location"]["address"]["area_code"] == "560001"
    assert intent["payment"]["@ondc/org/buyer_app_finder_fee_amount"] == "2.5"
    assert intent["payment"]["@ondc/org/buyer_app_finder_fee_type"] == "percent"


def _on_search(provider_key: str) -> dict:
    return {
        "context": {
            "bpp_id": "seller.example.com",
            "bpp_uri": "https://seller.example.com/ondc",
            "transaction_id": "T-1",
        },
        "message": {
            "catalog": {
                provider_key: [
                    {
                        "id": "P1",
                        "descriptor": {"name": "Corner Store"},
                        "items": [
                            {
                                "id": "I1",
                                "descriptor": {"name": "Full Cream Milk 1L"},
                                "price": {"value": "62", "currency": "INR"},
                                "location_id": "L1",
                                "fulfillment_id": "F1",
                            }
                        ],
                    }
                ]
            }
        },
    }


def test_parse_catalog_v1_2x_shape():
    parsed = parse_catalog(_on_search("bpp/providers"))
    assert parsed["bpp_id"] == "seller.example.com"
    assert parsed["bpp_uri"] == "https://seller.example.com/ondc"
    assert parsed["transaction_id"] == "T-1"
    assert parsed["providers"][0]["id"] == "P1"
    item = parsed["providers"][0]["items"][0]
    assert item["id"] == "I1" and item["name"] == "Full Cream Milk 1L"
    assert item["price"] == "62" and item["location_id"] == "L1"


def test_parse_catalog_v2_shape():
    parsed = parse_catalog(_on_search("providers"))
    assert parsed["providers"][0]["items"][0]["id"] == "I1"


def test_select_message_from_chosen_item():
    msg = select_message("P1", ["I1"], location_ids=["L1"], fulfillment_id="F1", gps="12.97,77.59", quantities={"I1": 2})
    order = msg["order"]
    assert order["provider"]["id"] == "P1"
    assert order["provider"]["locations"] == [{"id": "L1"}]
    assert order["items"][0] == {"id": "I1", "quantity": {"count": 2}, "fulfillment_id": "F1"}
    assert order["fulfillments"][0]["end"]["location"]["gps"] == "12.97,77.59"
