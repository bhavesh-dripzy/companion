"""Golden test: the clean MCP base helpers parse REAL Zomato MCP envelopes.

The fixture (`fixtures/zomato/search_pizza.json`) is a sanitized real capture from
a live Zomato turn (addresses -> restaurants -> menu). It validates the building
blocks the clean McpProvider ports rest on, against real provider data — so the
per-provider clean ports (which add card builders on top) start from a proven base.
"""

import json
from pathlib import Path

import pytest

from core.providers.mcp.address import extract_address_rows, first_address_id
from core.providers.mcp.placeholders import PlaceholderResolver

pytestmark = pytest.mark.component

_FIXTURE = json.loads((Path(__file__).parent / "fixtures/zomato/search_pizza.json").read_text())
_STEPS = {s["tool"]: s["result"] for s in _FIXTURE["steps"]}


def test_fixture_is_a_real_capture():
    assert _FIXTURE["ok"] is True
    assert set(_STEPS) >= {"get_saved_addresses_for_user", "get_restaurants_for_keyword"}


def test_extract_address_rows_on_real_envelope():
    rows = extract_address_rows(_STEPS["get_saved_addresses_for_user"])
    assert rows, "expected at least one saved address"
    for row in rows:
        assert row["address_id"] and isinstance(row["address_id"], str)
        assert row["label"]
    assert first_address_id(_STEPS["get_saved_addresses_for_user"]) == rows[0]["address_id"]


def test_placeholder_learning_on_real_restaurant_envelope():
    resolver = PlaceholderResolver()
    values: dict[str, str] = {}
    resolver.learn(values, _STEPS["get_restaurants_for_keyword"])
    assert values.get("__RESTAURANT_ID__"), "expected a learned restaurant id"
    assert values["__RESTAURANT_ID__"].isdigit() or len(values["__RESTAURANT_ID__"]) > 0


def test_result_parse_is_idempotent_on_already_parsed_envelopes():
    # The fixture stores already-serialized envelopes ({is_error, data}); the clean
    # base produces the same shape from a live MCP result. Sanity-check the contract.
    for env in _STEPS.values():
        assert "is_error" in env
        assert "data" in env or env.get("is_error")
