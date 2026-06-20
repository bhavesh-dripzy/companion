"""Zepto narrator digest reports rupees, not raw paise (prod test finding F1)."""

import pytest

from core.providers.plugins.zepto.digest import summarize

pytestmark = pytest.mark.component


def test_search_digest_is_rupees_not_paise():
    env = {
        "is_error": False,
        "data": {
            "products": [
                {
                    "productVariantId": "P1",
                    "storeProductId": "S1",
                    "name": "Amul Taaza Toned Milk",
                    "price": 2600,  # paise
                    "packSize": "500 ml",
                }
            ]
        },
    }
    out = summarize("search_products", env)
    assert "₹26" in out and "2600" not in out
    assert "Amul Taaza Toned Milk" in out


def test_cart_digest_totals_in_rupees():
    env = {
        "is_error": False,
        "data": {
            "items": [
                {
                    "productVariantId": "P1",
                    "storeProductId": "S1",
                    "name": "Milk",
                    "quantity": 2,
                    "price": 2600,
                }
            ],
            "isEmpty": False,
        },
    }
    out = summarize("view_cart", env)
    assert "Milk ×2" in out and "₹26" in out
    assert "₹52" in out  # 2600 paise x 2 = ₹52


def test_non_search_tool_defers_to_base_digest():
    assert summarize("get_payment_methods", {"is_error": False, "data": {"methods": []}}) is None


def test_error_envelope_yields_none():
    assert summarize("search_products", {"is_error": True, "data": {}}) is None
