"""Unit tests for Swiggy MCP executor helpers (no live network)."""

from app.services.food_response_builder import saved_address_label_from_mcp_item
from app.services.swiggy_mcp_executor import _first_address_id_swiggy


def test_saved_address_label_swiggy_get_addresses_shape():
    """Live Swiggy ``get_addresses`` uses id + addressLine + addressTag (camelCase)."""
    item = {
        "id": "d7o708sscnecd5k4qudg",
        "addressLine": "Bhavesh: 1201, M3M One Key Resiments, Golf Course Extension Road, Sector 67, Gurugram",
        "phoneNumber": "****7877",
        "addressCategory": "Work",
        "addressTag": "M3mmm",
    }
    label = saved_address_label_from_mcp_item(item)
    assert "M3mmm" in label
    assert "Gurugram" in label


def test_first_address_id_swiggy_user_addresses():
    payload = {
        "data": {
            "result": {
                "user_addresses": [
                    {"id": "addr-99", "label": "Home"},
                ]
            }
        }
    }
    assert _first_address_id_swiggy(payload) == "addr-99"


def test_first_address_id_swiggy_flat_addresses():
    payload = {
        "data": {
            "addresses": [{"addressId": "a1", "name": "Work"}],
        }
    }
    assert _first_address_id_swiggy(payload) == "a1"
