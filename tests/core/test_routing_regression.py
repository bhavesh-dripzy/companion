"""Regression suite: ensures existing routing, fan-out, and provider-selection
behaviour does not degrade when new tools or providers are added.

Three layers, in order of cost and fidelity:

  1. Unit tests (always run, no LLM) — deterministic parse_routing, fan-out
     expansion, cart stickiness, and previous-provider extraction.
  2. Integration tests (SARVAM_API_KEY env var → CI, else skip) — calls the
     real Route-stage orchestrator for every known domain group.
  3. Fixture file — machine-readable golden routing table; a separate
     `scripts/diff_routing.py` can print the delta when a PR changes behaviour.

Why this catches the degradation you described
──────────────────────────────────────────────
When a new provider (e.g. BlaBlaCar → carpool group) or a new task_type is
added, two failure modes are most common:

  (a) Deterministic: _AGNOSTIC_GROUPS or KNOWN_FOOD or _SOLE_PROVIDER or
      expand_agnostic is updated but an existing case silently changes outcome.
      Covered by layer-1 unit tests.
  (b) Semantic: the orchestrator system prompt grows with new tool
      descriptions and the LLM starts mis-classifying an old query
      (e.g. "order biryani" starts routing to district instead of zomato).
      Covered by layer-2 integration tests calling the real LLM.

PR authors adding a new provider MUST:
  • Add the new provider id to the appropriate KNOWN_* set.
  • Add the new provider to _AGNOSTIC_GROUPS if it belongs to a fan-out group.
  • Add at least one new case to _SOLE_PROVIDER if it is the sole provider
    for a task_type.
  • Add golden cases covering the new provider to this file.
  • If a new domain group (e.g. travel, health) is introduced, add a new
    L2 integration test for that group.

If a PR breaks an existing golden case without updating it, CI fails — the
failure is loud and the golden case list tells you exactly which behaviour
regressed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from core.contracts.routing import RoutingDecision
from core.llm.config import resolve_llm_from_env
from core.pipeline.orchestration import (
    _AGNOSTIC_GROUPS,
    _SOLE_PROVIDER,
    KNOWN_FOOD,
    expand_agnostic,
    extract_previous_provider,
    fallback_routing,
    message_is_transactional,
    message_names_provider,
    parse_routing,
)
from core.pipeline.routing_corrections import active_cart_provider, stick_to_active_cart

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 – Deterministic unit tests
# These run in every CI run without an API key and catch regressions in
# the rule-based routing logic.
# ─────────────────────────────────────────────────────────────────────────────


# ── parse_routing ────────────────────────────────────────────────────────────

class TestParseRoutingFood:
    @pytest.mark.parametrize("raw,expected", [
        ({"task_type": "food_delivery", "food_providers": ["zomato"]}, ["zomato"]),
        ({"task_type": "food_delivery", "food_providers": ["swiggy_food"]}, ["swiggy_food"]),
        ({"task_type": "food_delivery", "food_providers": ["swiggy_instamart"]}, ["swiggy_instamart"]),
        ({"task_type": "food_delivery", "food_providers": ["zepto"]}, ["zepto"]),
        ({"task_type": "food_delivery", "food_providers": ["district"]}, ["district"]),
        # Unknown provider names are dropped
        ({"task_type": "food_delivery", "food_providers": ["zomato", "bogus", "zepto"]}, ["zomato", "zepto"]),
        # Unknown task type falls back to food_delivery
        ({"task_type": "nonsense"}, "food_delivery"),
        # Empty food_providers → previous provider or zomato default
        ({"task_type": "food_delivery", "food_providers": []}, ["zomato"]),
    ])
    def test_food_providers(self, raw: dict[str, Any], expected: Any):
        result = parse_routing(raw, None)
        assert result.task_type == "food_delivery"
        if isinstance(expected, list):
            assert result.providers == expected
        else:
            assert result.task_type == expected

    def test_empty_food_providers_uses_previous_when_valid(self):
        result = parse_routing({"task_type": "food_delivery", "food_providers": []}, "zepto")
        assert result.providers == ["zepto"]

    def test_unknown_task_defaults_to_food_delivery(self):
        result = parse_routing({"task_type": "unknown_task"}, "swiggy_food")
        assert result.task_type == "food_delivery"
        assert result.providers == ["swiggy_food"]


class TestParseRoutingGoingOut:
    def test_district_is_only_provider_for_going_out(self):
        raw = {"task_type": "going_out", "food_providers": ["zomato", "district"]}
        result = parse_routing(raw, None)
        assert result.task_type == "going_out"
        assert result.providers == ["district"]

    def test_going_out_with_no_district_defaults_to_district(self):
        raw = {"task_type": "going_out", "food_providers": []}
        result = parse_routing(raw, None)
        assert result.providers == ["district"]


class TestParseRoutingSoleProvider:
    @pytest.mark.parametrize("task,expected", [
        ("phone_call", "twilio"),
        ("maps_query", "maps"),
        ("reminder_calendar", "calendar"),
        ("whatsapp_message", "whatsapp"),
        ("data_lookup", "serp"),
        ("carpool", "blablacar"),
    ])
    def test_sole_provider_tasks(self, task: str, expected: str):
        result = parse_routing({"?task_type": task}, None)
        # _SOLE_PROVIDER only keys by exact task name
        actual = _SOLE_PROVIDER.get(task)
        assert actual == expected, f"_SOLE_PROVIDER[{task}] = {actual}, expected {expected}"
        result2 = parse_routing({"task_type": task}, None)
        assert result2.providers == [expected]


class TestParseRoutingFinance:
    def test_known_finance_providers(self):
        result = parse_routing({"task_type": "finance_investing", "finance_providers": ["kite"]}, None)
        assert result.providers == ["kite"]
        result2 = parse_routing({"task_type": "finance_investing", "finance_providers": ["groww"]}, None)
        assert result2.providers == ["groww"]

    def test_unknown_finance_provider_defaults_to_kite(self):
        result = parse_routing({"task_type": "finance_investing", "finance_providers": []}, None)
        assert result.providers == ["kite"]


class TestParseRoutingHealth:
    def test_health_provider(self):
        result = parse_routing({"task_type": "health_nutrition", "health_providers": ["daily_calorie_tracker"]}, None)
        assert result.providers == ["daily_calorie_tracker"]


# ── message_names_provider ───────────────────────────────────────────────────

class TestMessageNamesProvider:
    @pytest.mark.parametrize("message,expected", [
        ("order from zomato", True),
        ("get it on swiggy", True),
        ("show instamart items", True),
        ("use zepto", True),
        ("order biryani", False),
        ("buy milk and eggs", False),
        ("what movies are playing", False),
        ("call my dad", False),
    ])
    def test_name_detection(self, message: str, expected: bool):
        assert message_names_provider(message) is expected


# ── message_is_transactional ───────────────────────────────────────────────

class TestMessageIsTransactional:
    @pytest.mark.parametrize("message,expected", [
        ("checkout my cart", True),
        ("check out my order", True),
        ("pay for the order", True),
        ("confirm checkout", True),
        ("cancel my order", True),
        ("track my order", True),
        ("reorder my last order", True),
        ("order biryani", False),
        ("show me biryani options", False),
        ("what is in my cart", False),  # "my cart" without transactional verb
    ])
    def test_transactional_detection(self, message: str, expected: bool):
        assert message_is_transactional(message) is expected


# ── expand_agnostic ─────────────────────────────────────────────────────────

class TestExpandAgnosticFood:
    """The two food domain groups: (zomato, swiggy_food) and (swiggy_instamart, zepto)."""

    def test_no_fanout_when_app_named(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
        result = expand_agnostic(base, "order biryani on zomato", enabled=True)
        assert result.providers == ["zomato"]

    def test_no_fanout_when_disabled(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
        result = expand_agnostic(base, "order biryani", enabled=False)
        assert result.providers == ["zomato"]

    def test_fanout_zomato_to_swiggy_food(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
        result = expand_agnostic(base, "order biryani", enabled=True)
        assert set(result.providers) == {"zomato", "swiggy_food"}

    def test_fanout_swiggy_instamart_to_zepto(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["swiggy_instamart"]}, None)
        result = expand_agnostic(base, "buy milk and bread", enabled=True)
        assert set(result.providers) == {"swiggy_instamart", "zepto"}

    def test_no_fanout_for_transactional_turns(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
        for msg in ("checkout my cart", "pay for the order", "track my order", "reorder"):
            result = expand_agnostic(base, msg, enabled=True)
            assert result.providers == ["zomato"], f"failed for: {msg}"

    def test_no_fanout_when_previous_provider_in_decision(self):
        """Mid-flow stickiness: orchestrator stuck with zomato → no re-fan-out."""
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, "zomato")
        result = expand_agnostic(base, "add paneer to my order", enabled=True)
        assert result.providers == ["zomato"]

    def test_fanout_zepto_grocery_to_instamart(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zepto"]}, None)
        result = expand_agnostic(base, "show me eggs and bread", enabled=True)
        assert set(result.providers) == {"zepto", "swiggy_instamart"}

    def test_client_allowed_providers_intersects_fanout(self):
        base = parse_routing({"task_type": "food_delivery", "food_providers": ["zomato"]}, None)
        result = expand_agnostic(base, "order biryani", enabled=True, allowed=["zepto", "zomato"])
        assert result.providers == ["zomato"]

    def test_district_never_fanned_out(self):
        """District is going-out-only; it must not enter the food fan-out groups."""
        base = parse_routing({"task_type": "going_out", "food_providers": ["district"]}, None)
        result = expand_agnostic(base, "movies near me", enabled=True)
        assert result.providers == ["district"]


# ── cart stickiness ─────────────────────────────────────────────────────────

_INSTAMART_CART = {"session_instamart_cart": {"items": [{"spin_id": "x"}]}}
_ZEPTO_CART = {"session_zepto_cart": {"items": [{"item_id": "y"}]}}


class TestCartStickiness:
    def test_active_cart_detected_instamart(self):
        assert active_cart_provider(_INSTAMART_CART) == "swiggy_instamart"

    def test_active_cart_detected_zepto(self):
        assert active_cart_provider(_ZEPTO_CART) == "zepto"

    def test_no_active_cart_returns_none(self):
        assert active_cart_provider({}) is None

    def test_grocery_leak_collapsed_to_active_cart(self):
        dec = RoutingDecision(task_type="food_delivery", providers=["zepto"], reasoning="r", previous_provider=None)
        result = stick_to_active_cart(dec, "remove the paneer", _INSTAMART_CART)
        assert result.providers == ["swiggy_instamart"]

    def test_grocery_fanout_collapsed_to_active_cart(self):
        dec = RoutingDecision(task_type="food_delivery", providers=["swiggy_instamart", "zepto"], reasoning="r", previous_provider=None)
        result = stick_to_active_cart(dec, "what's in my cart", _INSTAMART_CART)
        assert result.providers == ["swiggy_instamart"]

    def test_named_provider_respected(self):
        dec = RoutingDecision(task_type="food_delivery", providers=["zepto"], reasoning="r", previous_provider=None)
        result = stick_to_active_cart(dec, "remove paneer from my zepto cart", _INSTAMART_CART)
        assert result.providers == ["zepto"]

    def test_no_cart_still_fans_out(self):
        dec = RoutingDecision(task_type="food_delivery", providers=["swiggy_instamart", "zepto"], reasoning="r", previous_provider=None)
        result = stick_to_active_cart(dec, "show me milk", {})
        assert set(result.providers) == {"swiggy_instamart", "zepto"}

    def test_non_grocery_route_untouched(self):
        dec = RoutingDecision(task_type="food_delivery", providers=["zomato"], reasoning="r", previous_provider=None)
        result = stick_to_active_cart(dec, "remove paneer from my zomato order", _INSTAMART_CART)
        assert result.providers == ["zomato"]


# ── extract_previous_provider ────────────────────────────────────────────────

class TestExtractPreviousProvider:
    def test_explicit_provider_field(self):
        history = [{"role": "assistant", "provider": "zepto"}]
        assert extract_previous_provider(history) == "zepto"

    def test_content_based_instamart(self):
        history = [{"role": "assistant", "content": "Here are your Instamart options"}]
        assert extract_previous_provider(history) == "swiggy_instamart"

    def test_content_based_zepto(self):
        history = [{"role": "assistant", "content": "Zepto has these options"}]
        assert extract_previous_provider(history) == "zepto"

    def test_content_based_district(self):
        history = [{"role": "assistant", "content": "Here are events from District"}]
        assert extract_previous_provider(history) == "district"

    def test_no_history_returns_none(self):
        assert extract_previous_provider(None) is None
        assert extract_previous_provider([]) is None

    def test_only_user_messages_returns_none(self):
        history = [{"role": "user", "content": "hi"}]
        assert extract_previous_provider(history) is None


# ── fallback_routing ────────────────────────────────────────────────────────

class TestFallbackRouting:
    def test_fallback_sticks_with_previous(self):
        assert fallback_routing("zepto").providers == ["zepto"]

    def test_fallback_kite_finance(self):
        assert fallback_routing("kite").task_type == "finance_investing"

    def test_fallback_null_defaults_to_zomato(self):
        assert fallback_routing(None).providers == ["zomato"]


# ── KNOWN_FOOD immutability ─────────────────────────────────────────────────

class TestKnownFoodExhaustiveness:
    """If a new food provider is added but NOT added to KNOWN_FOOD, routing
    for that provider silently fails (it gets stripped in parse_routing).
    This test exists to make that explicit and checkable."""

    ALL_FOOD_PROVIDERS_FROM_SEED: frozenset[str] = frozenset({
        "zomato", "swiggy_food", "swiggy_instamart", "zepto", "district",
    })

    def test_all_seed_food_providers_are_in_known_food(self):
        missing = self.ALL_FOOD_PROVIDERS_FROM_SEED - KNOWN_FOOD
        assert not missing, f"seed food providers missing from KNOWN_FOOD: {missing}"

    def test_known_food_only_contains_valid_providers(self):
        """Guard rail: no typos, no fantasy providers sneaking into KNOWN_FOOD."""
        valid = self.ALL_FOOD_PROVIDERS_FROM_SEED | frozenset({"blablacar"})
        invalid = KNOWN_FOOD - valid
        # blablacar is carpool, not food; district is KNOWN_FOOD but not a delivery food provider
        # (it's in KNOWN_FOOD for parse_routing routing-group purposes, not delivery)
        assert not invalid, f"invalid entries in KNOWN_FOOD: {invalid}"


# ── _AGNOSTIC_GROUPS exhaustiveness ─────────────────────────────────────────

class TestAgnosticGroupsCoverage:
    """Every provider in a fan-out group must be in KNOWN_FOOD.
    Every KNOWN_FOOD provider that participates in fan-out must be in a group."""

    def test_all_group_members_are_known_food(self):
        for group in _AGNOSTIC_GROUPS:
            for p in group:
                assert p in KNOWN_FOOD, f"{p} in _AGNOSTIC_GROUPS but not in KNOWN_FOOD"

    def test_groups_cover_meal_and_grocery_pairs(self):
        """There must be exactly two groups: (zomato, swiggy_food) and (swiggy_instamart, zepto)."""
        flat = {p for g in _AGNOSTIC_GROUPS for p in g}
        assert flat == {"zomato", "swiggy_food", "swiggy_instamart", "zepto"}, \
            f"_AGNOSTIC_GROUPS = {_AGNOSTIC_GROUPS}, expected two meal+grocery pairs"


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 – Integration tests calling the real Route-stage orchestrator
# Runs in CI only when SARVAM_API_KEY is available.
# Skips gracefully (passes) in local dev without a key.
# ─────────────────────────────────────────────────────────────────────────────

_LLM, _ = resolve_llm_from_env()
_SKIP_LIVE = pytest.mark.skipif(_LLM is None, reason="no LLM API key configured")


def _classify_via_orchestrator(message: str, previous_provider: str | None = None) -> RoutingDecision:
    """Call the real orchestrator LLM and return the parsed RoutingDecision."""
    from core.llm.client import complete_json_object
    from core.pipeline.orchestration import frame_message, parse_routing
    from core.pipeline.stages.route import _orchestrator_prompt

    framed = frame_message(message, previous_provider)
    raw = asyncio.run(
        complete_json_object(user_message=framed, system_prompt=_orchestrator_prompt(), llm=_LLM)
    )
    return parse_routing(raw, previous_provider)


# Golden routing table: (message, previous_provider, expected_task_type, expected_providers)
# These are the cases that must NOT regress as new tools/providers are added.
_GOLDEN_CASES = [
    # ── Restaurant food ──────────────────────────────────────────────────
    ("order biryani", None, "food_delivery", ["zomato"]),
    ("order biryani", "zomato", "food_delivery", ["zomato"]),          # stickiness
    ("show me pizza options", None, "food_delivery", ["zomato"]),
    ("find good restaurants in Koramangala", None, "food_delivery", ["zomato"]),
    ("order from Haldiram's", None, "food_delivery", ["zomato"]),
    ("get biryani delivered", None, "food_delivery", ["zomato"]),
    ("checkout my cart", None, "food_delivery", ["zomato"]),           # transactional

    # ── Grocery ──────────────────────────────────────────────────────────
    ("buy milk", None, "food_delivery", ["swiggy_instamart"]),         # generic grocery
    ("order groceries", None, "food_delivery", ["swiggy_instamart"]),
    ("show me eggs and bread", None, "food_delivery", ["swiggy_instamart"]),
    ("need atta and rice", None, "food_delivery", ["swiggy_instamart"]),
    ("order on Zepto", None, "food_delivery", ["zepto"]),              # named Zepto
    ("show Instamart items", None, "food_delivery", ["swiggy_instamart"]),  # named Instamart

    # ── Compare ──────────────────────────────────────────────────────────
    ("compare biryani on Zomato and Swiggy", None, "food_delivery", None),  # None = any order ok

    # ── Going out ────────────────────────────────────────────────────────
    ("movies near Koramangala", None, "going_out", ["district"]),
    ("book a table for 2 at an Italian restaurant", None, "going_out", ["district"]),
    ("IMAX showtimes tonight", None, "going_out", ["district"]),
    ("comedy shows this weekend", None, "going_out", ["district"]),
    ("what concerts are happening near me", None, "going_out", ["district"]),
    ("things to do this weekend", None, "going_out", ["district"]),
    ("find events in Bangalore", None, "going_out", ["district"]),

    # ── Finance ──────────────────────────────────────────────────────────
    ("show my Zerodha holdings", None, "finance_investing", ["kite"]),
    ("what is my Nifty portfolio", None, "finance_investing", ["kite"]),
    ("place a buy order for Infosys", None, "finance_investing", ["kite"]),

    # ── Health ───────────────────────────────────────────────────────────
    ("log my lunch", None, "health_nutrition", ["daily_calorie_tracker"]),
    ("calories in paneer", None, "health_nutrition", ["daily_calorie_tracker"]),
    ("weekly calorie report", None, "health_nutrition", ["daily_calorie_tracker"]),

    # ── Maps ─────────────────────────────────────────────────────────────
    ("how long to reach MG Road", None, "maps_query", []),
    ("distance from here to the airport", None, "maps_query", []),
    ("ETA to office by car", None, "maps_query", []),

    # ── Phone ────────────────────────────────────────────────────────────
    ("call my father at 9910295298", None, "phone_call", ["twilio"]),

    # ── Data lookup ──────────────────────────────────────────────────────
    ("what is the latest in geopolitics", None, "data_lookup", []),    # news regression case
    ("flights from Delhi to Mumbai on Friday", None, "data_lookup", []),
    ("hotels in Goa under 5000", None, "data_lookup", []),
    ("best price for AirPods on Amazon", None, "data_lookup", []),
    ("show me some videos on the indian economy", None, "data_lookup", []),  # video regression case

    # ── Normal / chit-chat ────────────────────────────────────────────────
    ("what is photosynthesis", None, "normal_query", []),
    ("hello", None, "normal_query", []),
    ("thanks", None, "normal_query", []),
    ("explain how inflation works", None, "normal_query", []),

    # ── Mid-flow stickiness ──────────────────────────────────────────────
    ("add paneer to my order", "zomato", "food_delivery", ["zomato"]),   # mid-flow: stay
    ("remove the paneer", "swiggy_instamart", "food_delivery", ["swiggy_instamart"]),
]


@_SKIP_LIVE
@pytest.mark.parametrize("message,previous,expected_task,expected_providers", _GOLDEN_CASES)
def test_orchestrator_golden_routing(message, previous, expected_task, expected_providers):
    """Verify every known domain case routes to the expected provider.

    This is the primary regression guard: when a new tool or provider is added,
    this test ensures existing routing decisions are unchanged."""
    result = _classify_via_orchestrator(message, previous)
    assert result.task_type == expected_task, \
        f"task_type: {message!r} → got {result.task_type}, expected {expected_task} (reasoning: {result.reasoning})"
    if expected_providers is not None:
        assert result.providers == expected_providers, \
            f"providers: {message!r} → got {result.providers}, expected {expected_providers}"


# ── Provider fan-out: adding a new provider must not break existing groups ─

@_SKIP_LIVE
def test_grocery_fanout_still_includes_both_instamart_and_zepto():
    """Regression: new providers being added to the grocery group must not
    accidentally collapse the fan-out to a single provider."""
    result = _classify_via_orchestrator("buy milk and eggs", None)
    assert set(result.providers) == {"swiggy_instamart", "zepto"}, \
        f"grocery fan-out broken: got {result.providers}"


@_SKIP_LIVE
def test_restaurant_fanout_still_includes_zomato_and_swiggy_food():
    """Regression: new providers being added to the meal group must not
    accidentally collapse the fan-out to a single provider."""
    result = _classify_via_orchestrator("show biryani options", None)
    assert set(result.providers) == {"zomato", "swiggy_food"}, \
        f"restaurant fan-out broken: got {result.providers}"


# ── Account / recall regression (prod 613cca04) ─────────────────────────────

@_SKIP_LIVE
@pytest.mark.parametrize("message", [
    "show me my saved addresses for instamart",
    "reorder my go-to items on instamart",
    "switch my instamart delivery address to Work",
    "show my past instamart orders",
])
def test_account_recall_routes_to_food_delivery(message: str):
    """Regression guard: account/recall intents must reach food_delivery
    (a live provider), not normal_query with empty providers."""
    result = _classify_via_orchestrator(message, None)
    assert result.task_type == "food_delivery", \
        f"{message!r} misrouted to {result.task_type} (reasoning: {result.reasoning})"
    assert result.providers, f"{message!r} routed to food_delivery but providers=[], dead-end"
