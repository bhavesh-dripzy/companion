"""An active grocery cart pins grocery turns to that provider (no leak to the other app)."""

from core.contracts.routing import RoutingDecision
from core.pipeline.orchestration import active_cart_provider, stick_to_active_cart

_INSTA_CART = {"session_instamart_cart": {"items": [{"spin_id": "x"}]}}


def _dec(providers):
    return RoutingDecision(
        task_type="food_delivery", providers=providers, reasoning="r", previous_provider=None
    )


def test_active_cart_detected():
    assert active_cart_provider(_INSTA_CART) == "swiggy_instamart"
    assert active_cart_provider({}) is None


def test_leak_collapsed_to_active_cart():
    # "remove the milk" routed to Zepto, but the cart is on Instamart -> pin to Instamart.
    out = stick_to_active_cart(_dec(["zepto"]), "remove the milk", _INSTA_CART)
    assert list(out.providers) == ["swiggy_instamart"]


def test_fanout_collapsed_to_active_cart():
    out = stick_to_active_cart(_dec(["swiggy_instamart", "zepto"]), "what's in my cart", _INSTA_CART)
    assert list(out.providers) == ["swiggy_instamart"]


def test_named_provider_respected():
    out = stick_to_active_cart(_dec(["zepto"]), "remove milk from my zepto cart", _INSTA_CART)
    assert list(out.providers) == ["zepto"]


def test_no_active_cart_still_fans_out():
    out = stick_to_active_cart(_dec(["swiggy_instamart", "zepto"]), "show me milk", {})
    assert list(out.providers) == ["swiggy_instamart", "zepto"]


def test_non_grocery_route_untouched():
    out = stick_to_active_cart(_dec(["zomato"]), "remove the milk", _INSTA_CART)
    assert list(out.providers) == ["zomato"]
