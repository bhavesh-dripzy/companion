"""Within a food turn, an explicitly named app overrides the orchestrator's misroute."""

from core.contracts.routing import RoutingDecision
from core.pipeline.routing_corrections import honor_named_providers


def _dec(providers, task="food_delivery"):
    return RoutingDecision(task_type=task, providers=providers, reasoning="r", previous_provider=None)


def test_named_instamart_overrides_zomato_misroute():
    out = honor_named_providers(_dec(["zomato"]), "add milk to my instamart cart")
    assert list(out.providers) == ["swiggy_instamart"]


def test_named_both_grocery_apps():
    out = honor_named_providers(_dec(["zomato"]), "compare milk on instamart and zepto")
    assert set(out.providers) == {"swiggy_instamart", "zepto"}


def test_no_name_left_unchanged():
    out = honor_named_providers(_dec(["zomato"]), "add milk to my cart")
    assert list(out.providers) == ["zomato"]


def test_chitchat_mention_not_promoted_to_food():
    # A normal_query mention of an app must NOT become a food turn.
    out = honor_named_providers(_dec([], task="normal_query"), "is zomato better than cooking?")
    assert list(out.providers) == []


def test_named_matches_routed_no_change():
    out = honor_named_providers(_dec(["zomato"]), "order pizza from zomato")
    assert list(out.providers) == ["zomato"]


def test_named_grocery_search_promoted_from_chitchat():
    # F18: orchestrator intermittently labels a named grocery search normal_query
    # and routes it nowhere — a named app + a food intent must reach the app.
    out = honor_named_providers(_dec([], task="normal_query"), "search zepto for milk")
    assert list(out.providers) == ["zepto"] and out.task_type == "food_delivery"


def test_address_listing_for_named_provider_promoted():
    # F10: "what addresses do I have on zepto" was denied; route it to Zepto.
    out = honor_named_providers(
        _dec([], task="normal_query"), "what delivery addresses do I have saved on zepto"
    )
    assert list(out.providers) == ["zepto"]


def test_finance_query_mentioning_zomato_not_hijacked():
    # Promotion is grocery-only: "Zomato stock price" must stay a data lookup,
    # not become a restaurant search (Zomato is a listed company).
    out = honor_named_providers(_dec([], task="data_lookup"), "what is zomato stock price today")
    assert list(out.providers) == []
