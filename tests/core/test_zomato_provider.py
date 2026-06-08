"""Golden test: the clean `ZomatoProvider` builds correct cards + session from REAL data.

Drives `ZomatoProvider` directly (not via the registry — the registry still wires
the LegacyMcpAdapter until the bootstrap swap) against the sanitized real capture
`fixtures/zomato/search_pizza.json`, asserting the cards + session LRU reflect the
real restaurant the live turn surfaced (Domino's, res_id 20324557). This validates
the clean execution-result -> card / session path against real provider responses.
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.zomato import ZomatoProvider

pytestmark = pytest.mark.component

_FIXTURE = json.loads((Path(__file__).parent / "fixtures/zomato/search_pizza.json").read_text())


def _provider_result() -> ProviderResult:
    results = [
        ToolResult(
            tool=step["tool"],
            provider="zomato",
            ok=not (step["result"] or {}).get("is_error"),
            arguments=step.get("arguments") or {},
            data=step["result"],
        )
        for step in _FIXTURE["steps"]
    ]
    return ProviderResult(provider="zomato", results=results, ok=_FIXTURE["ok"])


def test_clean_provider_builds_restaurant_cards_from_real_data():
    cards = ZomatoProvider().build_attachments(_provider_result())
    assert cards, "expected at least one card from the real restaurant search"
    blob = json.dumps([c.to_wire() for c in cards])
    assert "20324557" in blob, "the real restaurant id from the live capture should appear"
    assert all(c.kind == "restaurant_cards" for c in cards)


def test_clean_provider_session_restaurants_round_trip():
    merged = ZomatoProvider().merge({}, _provider_result())
    restaurants = merged.get("session_restaurants") or []
    assert restaurants, "expected session_restaurants carried forward from real data"
    assert restaurants[0]["res_id"] == "20324557", "most-recent restaurant should lead the LRU"
    # Lean projection: only the planner-facing fields, no bulky items[]/images.
    assert all("items" not in r and "res_image" not in r for r in restaurants)


def test_clean_provider_session_lru_dedupes_and_caps():
    prior = [{"res_id": "20324557", "name": "Old Domino's"}, {"res_id": "999", "name": "Other"}]
    merged = ZomatoProvider().merge({"session_restaurants": prior}, _provider_result())
    restaurants = merged["session_restaurants"]
    ids = [r["res_id"] for r in restaurants]
    assert ids[0] == "20324557" and ids.count("20324557") == 1, "fresh hit dedupes the stale dup"
    assert restaurants[0]["name"] != "Old Domino's", "fresh entry overwrites the stale one"
    assert len(restaurants) == 8, "the LRU is capped at the remembered-restaurants limit"


def test_clean_provider_session_lru_retains_prior_tail_under_cap():
    # A small fresh result (1 hit) leaves room, so prior entries survive in the tail.
    one_hit = ProviderResult(
        provider="zomato",
        results=[
            ToolResult(
                tool="get_restaurants_for_keyword",
                provider="zomato",
                ok=True,
                data={"data": {"results": [{"res_id": "111", "name": "Fresh Place"}]}},
            )
        ],
    )
    prior = [{"res_id": "999", "name": "Old Place"}]
    merged = ZomatoProvider().merge({"session_restaurants": prior}, one_hit)
    ids = [r["res_id"] for r in merged["session_restaurants"]]
    assert ids == ["111", "999"], "fresh leads, prior retained in the tail"
