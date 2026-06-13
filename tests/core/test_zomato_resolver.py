"""Name-aware __RESTAURANT_ID__ learning for Zomato's fuzzy keyword search.

Guards prod chat d21a895d (2026-06-12): "Show me the menu of Ramen Republic,
Sector 31" fetched an all-biryani menu because the default learner grabbed the
fuzzy-ranked first hit (a biryani outlet) instead of the named restaurant.
"""

import pytest

from core.providers.plugins.zomato.placeholders import ZomatoResolver

pytestmark = pytest.mark.unit


def _search_payload(*restaurants: tuple[str, str]) -> dict:
    """A get_restaurants_for_keyword envelope: data.results[] in rank order."""
    return {"data": {"results": [{"name": n, "res_id": r} for n, r in restaurants]}}


def _learn_search(keyword: str, payload: dict) -> dict[str, str]:
    """Mirror the execute loop: learn_from_args(search) then learn(response)."""
    resolver = ZomatoResolver()
    values: dict[str, str] = {}
    resolver.clear_stale(values, "get_restaurants_for_keyword")
    resolver.learn_from_args(values, "get_restaurants_for_keyword", {"keyword": keyword})
    resolver.learn(values, payload)
    return values


def test_named_search_picks_matching_outlet_not_first_hit():
    # Zomato ranks a biryani place first; the user named "Ramen Republic".
    payload = _search_payload(
        ("Biryani Blues", "22197705"),
        ("Pizza Hut", "111"),
        ("Ramen Republic", "98765"),
    )
    values = _learn_search("Ramen Republic, Sector 31", payload)
    assert values["__RESTAURANT_ID__"] == "98765"


def test_generic_cuisine_search_keeps_first_hit():
    # "pizza" names no single outlet — keep the fuzzy-ranked first result.
    payload = _search_payload(
        ("Domino's Pizza", "20324557"),
        ("Pizza Wings", "21109389"),
    )
    values = _learn_search("pizza", payload)
    assert values["__RESTAURANT_ID__"] == "20324557"


def test_multiword_cuisine_search_keeps_first_hit():
    # "veg pizza" still names no outlet (no result's full name ⊆ the keyword).
    payload = _search_payload(
        ("Domino's Pizza", "20324557"),
        ("La Pino'z Pizza", "21109389"),
    )
    values = _learn_search("veg pizza", payload)
    assert values["__RESTAURANT_ID__"] == "20324557"


def test_most_specific_name_wins_over_sibling_outlet():
    payload = _search_payload(
        ("Ramen Republic Express", "1"),
        ("Ramen Republic", "2"),
    )
    # "Ramen Republic" ⊆ keyword but "...Express" is not → the exact name wins.
    values = _learn_search("Ramen Republic", payload)
    assert values["__RESTAURANT_ID__"] == "2"


def test_name_hint_is_transient_and_not_persisted():
    resolver = ZomatoResolver()
    values: dict[str, str] = {}
    resolver.learn_from_args(values, "get_restaurants_for_keyword", {"keyword": "Ramen Republic"})
    assert "__RESTAURANT_NAME_HINT__" in values  # in-turn only
    # The hint is declared transient, so mcp_base strips it before persistence.
    assert "__RESTAURANT_NAME_HINT__" in resolver.transient
    # A subsequent non-search tool clears it so it can't bias later lists.
    resolver.learn_from_args(values, "get_menu_items_listing", {"res_id": "98765"})
    assert "__RESTAURANT_NAME_HINT__" not in values


def test_explicit_res_id_in_session_is_not_overridden():
    # When the planner already has __RESTAURANT_ID__ (reused from session), a
    # later search response must not clobber it.
    resolver = ZomatoResolver()
    values = {"__RESTAURANT_ID__": "55555"}
    resolver.learn_from_args(values, "get_restaurants_for_keyword", {"keyword": "Ramen Republic"})
    resolver.learn(values, _search_payload(("Ramen Republic", "98765")))
    assert values["__RESTAURANT_ID__"] == "55555"
