"""Unit tests for Swiggy MCP executor helpers (no live network)."""

from app.services.swiggy_mcp_executor import _first_address_id_swiggy


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
