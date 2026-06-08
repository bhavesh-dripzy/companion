"""Contract tests: the clean Zomato cart + payment card builders.

Cart-create and checkout are mutating, so these drive representative envelopes
shaped exactly as the legacy executor's `_extract_cart_snapshot` /
`_extract_payment_from_checkout` read them (single create_cart node with items;
checkout images[0].data_b64). They lock the clean builders to that contract.
"""

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.zomato import ZomatoProvider
from core.providers.plugins.zomato.cart_cards import build_cart_cards, cart_snapshot
from core.providers.plugins.zomato.payment_cards import build_payment_cards

pytestmark = pytest.mark.component

# 1x1 PNG — valid base64 so the (best-effort) QR decode runs and finds no UPI URI.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_CREATE_CART = {
    "is_error": False,
    "data": {
        "result": {
            "cart_id": "cart_abc123",
            "res_id": "20324557",
            "restaurant_name": "Domino's Pizza",
            "restaurant_rating": 4.2,
            "item_total": 318,
            "final_amount": 357,
            "eta": "30 MINS",
            "charge_breakdown": {
                "base_charges": [{"name": "Delivery", "amount": 29}],
                "taxes": [{"name": "GST", "amount": 10}],
            },
            "items": [
                {
                    "quantity": 2,
                    "item": {
                        "variant_id": "v_1048395714",
                        "item_id": "ctl_785568411",
                        "name": "Classic Stuffed Garlic Bread",
                        "price": 159,
                        "image_url": "https://b.zmtcdn.com/x.jpg",
                        "item_tags": ["veg"],
                    },
                    "add_ons": [
                        {"name": "Extra Cheese", "add_on_group_name": "Add-ons", "quantity": 1}
                    ],
                }
            ],
        }
    },
}

_CHECKOUT = {"is_error": False, "images": [{"data_b64": _PNG_B64, "mime_type": "image/png"}]}


def _result(*steps) -> ProviderResult:
    return ProviderResult(
        provider="zomato",
        results=[
            ToolResult(tool=t, provider="zomato", ok=not (d or {}).get("is_error"), data=d)
            for t, d in steps
        ],
    )


def test_cart_snapshot_normalizes_the_create_cart_envelope():
    snap = cart_snapshot(_CREATE_CART)
    assert snap is not None
    assert snap["cart_id"] == "cart_abc123" and snap["res_id"] == "20324557"
    assert snap["restaurant_name"] == "Domino's Pizza" and snap["final_amount"] == 357
    assert len(snap["items"]) == 1
    item = snap["items"][0]
    assert item["variant_id"] == "v_1048395714" and item["quantity"] == 2
    assert item["add_ons"][0]["name"] == "Extra Cheese"
    assert snap["charge_breakdown"]["taxes"][0]["amount"] == 10


def test_build_cart_cards_emits_one_card_per_create_cart():
    cards = build_cart_cards(_result(("create_cart", _CREATE_CART)).results)
    assert len(cards) == 1
    wire = cards[0].to_wire()
    assert wire["kind"] == "cart_card" and wire["provider"] == "zomato"
    assert wire["source"] == "zomato.create_cart"
    assert wire["cart"]["cart_id"] == "cart_abc123"


def test_cart_card_skipped_when_no_items_or_error():
    assert build_cart_cards(_result(("create_cart", {"is_error": True})).results) == []
    empty = {"is_error": False, "data": {"result": {"cart_id": "c1", "items": []}}}
    assert build_cart_cards(_result(("create_cart", empty)).results) == []


def test_build_payment_cards_lifts_the_qr_image():
    cards = build_payment_cards(_result(("checkout_cart", _CHECKOUT)).results)
    assert len(cards) == 1
    wire = cards[0].to_wire()
    assert wire["kind"] == "image" and wire["source"] == "zomato.checkout_cart"
    assert wire["data_b64"] == _PNG_B64 and wire["mime_type"] == "image/png"
    assert "Scan with any UPI app" in wire["caption"]


def test_provider_render_order_payment_menu_restaurant_cart():
    # All four kinds present -> payment, (menu), (restaurant), cart in that order.
    result = _result(("checkout_cart", _CHECKOUT), ("create_cart", _CREATE_CART))
    kinds = [c.kind for c in ZomatoProvider().build_attachments(result)]
    assert kinds == ["image", "cart_card"], "payment leads, cart trails"


def test_provider_merge_stores_session_cart_replacement():
    merged = ZomatoProvider().merge({}, _result(("create_cart", _CREATE_CART)))
    assert merged["session_cart"]["cart_id"] == "cart_abc123"
