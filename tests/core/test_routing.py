"""Unit tests for orchestrator parsing, stickiness, and #14 agnostic fan-out."""

import pytest

from core.pipeline.orchestration import (
    expand_agnostic,
    extract_previous_provider,
    fallback_routing,
    message_is_transactional,
    message_names_provider,
    parse_routing,
)

pytestmark = pytest.mark.unit


def test_parse_routing_food_and_going_out():
    food = parse_routing(
        {"task_type": "food_delivery", "food_providers": ["zomato", "bogus"]}, None
    )
    assert food.task_type == "food_delivery" and food.providers == ["zomato"]
    out = parse_routing({"task_type": "going_out", "food_providers": ["zomato"]}, None)
    assert out.providers == ["district"]  # going-out is District-only


def test_parse_routing_unknown_task_defaults_to_food():
    decision = parse_routing({"task_type": "nonsense"}, "swiggy_food")
    assert decision.task_type == "food_delivery"
    assert decision.providers == ["swiggy_food"]  # empty food list -> previous provider


def test_parse_routing_sole_provider_tasks():
    assert parse_routing({"task_type": "cab_booking"}, None).providers == []  # uber removed
    assert parse_routing({"task_type": "phone_call"}, None).providers == ["twilio"]
    assert parse_routing({"task_type": "maps_query"}, None).providers == ["maps"]
    # normal_query is serp-backed: the planner sees web_search/search_news and decides
    # whether to fetch, so an informational chat turn is never starved of tools.
    assert parse_routing({"task_type": "normal_query"}, None).providers == ["serp"]


def test_fallback_routing_sticks_with_previous():
    assert fallback_routing("zepto").providers == ["zepto"]
    assert fallback_routing("kite").task_type == "finance_investing"
    assert fallback_routing(None).providers == ["zomato"]


def test_extract_previous_provider():
    assert extract_previous_provider([{"role": "assistant", "provider": "zepto"}]) == "zepto"
    history = [{"role": "assistant", "content": "Here are Instamart options"}]
    assert extract_previous_provider(history) == "swiggy_instamart"
    assert extract_previous_provider([{"role": "user", "content": "hi"}]) is None


def test_message_names_provider():
    assert message_names_provider("order from zomato")
    assert message_names_provider("get it on swiggy")
    assert not message_names_provider("order biryani")


def test_expand_agnostic_fans_out_within_domain_group():
    base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
    fanned = expand_agnostic(base, "order biryani", enabled=True)
    assert fanned.providers == ["zomato", "swiggy_food"]
    grocery = parse_routing(
        {"task_type": "food_delivery", "food_providers": ["swiggy_instamart"]}, None
    )
    assert expand_agnostic(grocery, "buy milk and bread", enabled=True).providers == [
        "swiggy_instamart",
        "zepto",
    ]
    # user named an app -> unchanged
    assert expand_agnostic(base, "order biryani on zomato", enabled=True).providers == ["zomato"]
    # disabled -> unchanged
    assert expand_agnostic(base, "order biryani", enabled=False).providers == ["zomato"]


def test_expand_agnostic_respects_allowed_and_stickiness():
    base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
    # client restricts providers -> intersected (here the group collapses back to one)
    limited = expand_agnostic(base, "order biryani", enabled=True, allowed=["zepto", "zomato"])
    assert limited.providers == ["zomato"]
    # mid-flow: orchestrator stuck with the previous provider -> no re-fan-out
    sticky = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, "zomato")
    assert expand_agnostic(sticky, "add it to my cart", enabled=True).providers == ["zomato"]


def test_expand_agnostic_never_fans_transactional_turns():
    base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
    for message in ("checkout my cart", "pay for the order", "cancel my order", "track my order"):
        assert expand_agnostic(base, message, enabled=True).providers == ["zomato"]
    assert message_is_transactional("confirm checkout my cart")
    assert not message_is_transactional("order biryani")
