"""Golden test: the redesign builds correct cards from REAL Zomato data.

Drives the registered Zomato provider's `build_attachments` with the sanitized
real capture (`fixtures/zomato/search_pizza.json`) and asserts the cards reflect
the real restaurant the live turn surfaced (Domino's, res_id 20324557). This
validates end-to-end card accuracy against real provider responses.
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.registry.bootstrap import build_registry

pytestmark = pytest.mark.component

_FIXTURE = json.loads((Path(__file__).parent / "fixtures/zomato/search_pizza.json").read_text())


def _provider_result() -> ProviderResult:
    results = [
        ToolResult(
            tool=step["tool"],
            provider="zomato",
            ok=not (step["result"] or {}).get("is_error"),
            data=step["result"],
        )
        for step in _FIXTURE["steps"]
    ]
    return ProviderResult(
        provider="zomato", results=results, ok=_FIXTURE["ok"], diagnostics=_FIXTURE
    )


def test_zomato_builds_restaurant_cards_from_real_data():
    zomato = build_registry().get("zomato")
    cards = zomato.build_attachments(_provider_result())
    assert cards, "expected at least one card from the real restaurant search"

    blob = json.dumps([c.to_wire() for c in cards])
    # The real restaurant the live turn cited (Domino's) must be in the cards.
    assert (
        "20324557" in blob
    ), "the real restaurant id from the live capture should appear in a card"
    assert any("restaurant" in c.kind.lower() for c in cards)


def test_session_restaurants_round_trip_from_real_diagnostics():
    # The executor's diagnostics carry the session_restaurants the planner reuses
    # next turn; the adapter merges them into the flat session bag.
    zomato = build_registry().get("zomato")
    merged = zomato.merge({}, _provider_result())
    restaurants = merged.get("session_restaurants") or []
    assert restaurants, "expected session_restaurants carried forward from real data"
    assert any(
        str(r.get("res_id") or r.get("restaurant_id") or "") == "20324557" for r in restaurants
    )
