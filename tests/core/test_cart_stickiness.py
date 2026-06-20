"""An active grocery app pins grocery cart/add turns to that provider (no leak)."""

import pytest

from core.contracts.routing import RoutingDecision
from core.pipeline.routing_corrections import (
    active_cart_provider,
    active_grocery_provider,
    stick_to_active_cart,
)

pytestmark = pytest.mark.unit

_INSTA_CART = {"session_instamart_cart": {"items": [{"spin_id": "x"}]}}
_ZEPTO_SEARCHED = {"session_zepto_products": [{"product_variant_id": "pv"}]}


def _dec(providers):
    return RoutingDecision(
        task_type="food_delivery", providers=providers, reasoning="r", previous_provider=None
    )


def test_active_cart_detected():
    assert active_cart_provider(_INSTA_CART) == "swiggy_instamart"
    assert active_cart_provider({}) is None


def test_active_grocery_provider_from_cart_then_products():
    assert active_grocery_provider(_INSTA_CART) == "swiggy_instamart"
    # No cart yet, but only Zepto was searched -> Zepto is the active grocery app.
    assert active_grocery_provider(_ZEPTO_SEARCHED) == "zepto"
    # Both searched, no cart -> ambiguous -> keep the fan-out.
    both = {**_ZEPTO_SEARCHED, "session_instamart_products": [{"spin_id": "y"}]}
    assert active_grocery_provider(both) is None
    assert active_grocery_provider({}) is None


def test_leak_collapsed_to_active_cart():
    # "remove the milk" routed to Zepto, but the cart is on Instamart -> Instamart.
    out = stick_to_active_cart(_dec(["zepto"]), "remove the milk", _INSTA_CART)
    assert list(out.providers) == ["swiggy_instamart"]


def test_fanout_collapsed_to_active_cart():
    out = stick_to_active_cart(_dec(["swiggy_instamart", "zepto"]), "what's in my cart", _INSTA_CART)
    assert list(out.providers) == ["swiggy_instamart"]


def test_named_provider_respected():
    out = stick_to_active_cart(_dec(["zepto"]), "remove milk from my zepto cart", _INSTA_CART)
    assert list(out.providers) == ["zepto"]


def test_no_active_grocery_still_fans_out():
    out = stick_to_active_cart(_dec(["swiggy_instamart", "zepto"]), "show me milk", {})
    assert list(out.providers) == ["swiggy_instamart", "zepto"]


def test_cart_op_reclaimed_from_non_grocery_route():
    """The leak fix: turns mis-routed to a non-grocery app (Zomato) while a grocery
    cart is live are reclaimed for that cart — including quantity edits phrased
    without any cart keyword ('set the milk to 5', 'bring it down to 2'), which a
    verb whitelist would miss."""
    for msg in ("remove the milk", "what's in my cart", "set the milk to 5",
                "bring the milk down to 2", "make it 2"):
        out = stick_to_active_cart(_dec(["zomato"]), msg, _INSTA_CART)
        assert list(out.providers) == ["swiggy_instamart"], msg


def test_show_my_cart_sticks_despite_browse_verb():
    """'show my cart' contains a browse verb but references the cart — it must stick
    to the active cart, not fan out."""
    out = stick_to_active_cart(_dec(["zomato"]), "show me my cart", _INSTA_CART)
    assert list(out.providers) == ["swiggy_instamart"]


def test_add_after_search_pins_to_searched_grocery():
    """Mode-A leak: after a Zepto search (no cart yet), the first add must stay on
    Zepto instead of defaulting to Instamart."""
    out = stick_to_active_cart(
        _dec(["swiggy_instamart", "zepto"]), "add a packet of milk to my cart", _ZEPTO_SEARCHED
    )
    assert list(out.providers) == ["zepto"]


def test_named_switch_overrides_active_cart():
    """An explicitly-named different app always wins over the active-cart pin — the
    user's escape hatch out of a grocery cart context."""
    out = stick_to_active_cart(_dec(["zomato"]), "order a pizza from zomato", _INSTA_CART)
    assert list(out.providers) == ["zomato"]


def test_browse_still_fans_out_with_active_cart():
    """A genuine browse ('show me bread') keeps its grocery price-comparison fan-out
    even when a cart is already active — only edits/views stick."""
    out = stick_to_active_cart(_dec(["swiggy_instamart", "zepto"]), "show me some bread", _INSTA_CART)
    assert list(out.providers) == ["swiggy_instamart", "zepto"]


def test_discovery_not_pinned_preserves_fanout():
    """Pre-cart discovery keeps its fan-out (only an explicit add pins)."""
    out = stick_to_active_cart(_dec(["swiggy_instamart", "zepto"]), "show me bread", _ZEPTO_SEARCHED)
    assert list(out.providers) == ["swiggy_instamart", "zepto"]
