"""Deterministic routing corrections applied after the orchestrator LLM.

The LLM orchestrator is unreliable on grocery (Instamart prod audit, 2026-06): it
misroutes explicitly-named apps to Zomato and bounces a single cart conversation
between Instamart and Zepto, so cart ops leak to the wrong app's cart. These
post-LLM passes fix the cases decidable from the message + session without trusting
the model. Imported by the Route stage.
"""

from __future__ import annotations

import re
from typing import Any

from core.contracts.routing import RoutingDecision
from core.pipeline.orchestration import message_names_provider

# Food/grocery apps that map unambiguously from a name to a provider. "swiggy" is
# intentionally excluded (ambiguous: Swiggy Food vs Instamart).
_NAMED_FOOD_PROVIDERS = {"instamart": "swiggy_instamart", "zepto": "zepto", "zomato": "zomato"}

# Grocery providers + the session key holding each one's live cart snapshot.
_GROCERY_CARTS = (
    ("swiggy_instamart", "session_instamart_cart"),
    ("zepto", "session_zepto_cart"),
)

# Grocery providers + the session key holding each one's shown-products LRU. Used
# to infer the active grocery app BEFORE a cart exists (e.g. right after a search),
# so the first "add … to cart" stays on the app the user was just browsing.
_GROCERY_PRODUCTS = (
    ("swiggy_instamart", "session_instamart_products"),
    ("zepto", "session_zepto_products"),
)

# Add / cart intent — used ONLY before a cart exists (to pin the first add to the
# lone searched grocery app). Once a cart exists we use the broader non-discovery
# rule below instead, because quantity edits are phrased endlessly ("set it to 5",
# "make it 2", "bring the milk down") and can't be whitelisted reliably.
_CART_OP_RE = re.compile(
    r"\b(cart|basket|checkout|check ?out|empty|clear)\b|\bmy (cart|order)\b|"
    r"\b(add|remove|delete|increase|decrease|reduce)\b"
)
# Browse/discovery verbs — these keep a grocery turn's price-comparison fan-out
# even when an app is already active (the user is comparing, not editing a cart).
_DISCOVERY_RE = re.compile(
    r"\b(search|show|find|browse|look|looking|recommend|suggest|compare|options|"
    r"explore|see|available)\b"
)
# A reference to the user's own cart/order — a "show my cart" must stay on the cart
# even though it contains a discovery verb ("show").
_CART_REF_RE = re.compile(r"\bcart\b|\bbasket\b|\bmy order\b")


def _is_cart_op(message: str) -> bool:
    return bool(_CART_OP_RE.search((message or "").lower()))


def _is_browse(message: str) -> bool:
    low = (message or "").lower()
    return bool(_DISCOVERY_RE.search(low)) and not _CART_REF_RE.search(low)


def _routed(decision: RoutingDecision, providers: list[str], note: str) -> RoutingDecision:
    return RoutingDecision(
        task_type="food_delivery",
        providers=providers,
        reasoning=f"{decision.reasoning} ({note})",
        previous_provider=decision.previous_provider,
    )


def honor_named_providers(decision: RoutingDecision, message: str) -> RoutingDecision:
    """Within a food turn, route to the app(s) the user explicitly named.

    The orchestrator sometimes ignores an explicit name — "add milk to my instamart
    cart" misrouted to Zomato live. Gated on a food_delivery task so a chit-chat
    mention of an app can never promote a non-food turn into one."""
    if decision.task_type != "food_delivery":
        return decision
    low = (message or "").lower()
    named: list[str] = []
    for name, pid in _NAMED_FOOD_PROVIDERS.items():
        if pid not in named and re.search(rf"\b{name}\b", low):
            named.append(pid)
    if not named or set(named) == set(decision.providers):
        return decision
    return _routed(decision, named, f"named {named}")


def active_cart_provider(legacy: dict[str, Any]) -> str | None:
    """The grocery provider whose cart currently holds items, or None."""
    if not isinstance(legacy, dict):
        return None
    for pid, key in _GROCERY_CARTS:
        cart = legacy.get(key)
        if isinstance(cart, dict) and cart.get("items"):
            return pid
    return None


def active_grocery_provider(legacy: dict[str, Any]) -> str | None:
    """The grocery app the user is currently working with.

    A live cart is the strongest signal; before any cart exists, fall back to the
    grocery app whose products were shown this session — but ONLY when exactly one
    has (both → genuinely ambiguous, keep the fan-out; neither → nothing to pin)."""
    if not isinstance(legacy, dict):
        return None
    cart_pid = active_cart_provider(legacy)
    if cart_pid:
        return cart_pid
    shown = [pid for pid, key in _GROCERY_PRODUCTS if legacy.get(key)]
    return shown[0] if len(shown) == 1 else None


def stick_to_active_cart(
    decision: RoutingDecision, message: str, legacy: dict[str, Any]
) -> RoutingDecision:
    """Keep one grocery conversation on the app the user is actually working with.

    The orchestrator scatters a single cart conversation across Instamart / Zepto /
    Zomato, so writes leak to the wrong app: "add milk to cart" after a Zepto search
    went to Instamart; "what's in my cart" / "set the milk to 5" went to Zomato.

    Two tiers, strongest signal first:
      * A LIVE CART exists → pin EVERY non-browse turn to that cart's app, even over
        a non-grocery mis-route. Quantity edits are phrased endlessly ("set it to 5",
        "make it 2", "bring the milk down") and can't be whitelisted, so we invert:
        only a genuine browse ("show me bread") fans out; everything else (edits,
        "what's in my cart", "remove the eggs") sticks.
      * NO CART YET, one grocery app searched → pin the first explicit add/cart turn
        to it, while plain discovery still fans out for price comparison.
    An explicitly-named app always wins (handled first)."""
    if message_names_provider(message):
        return decision
    cart_pid = active_cart_provider(legacy)
    if cart_pid:
        if set(decision.providers) == {cart_pid} or _is_browse(message):
            return decision
        return _routed(decision, [cart_pid], f"pinned to active {cart_pid} cart")
    sticky = active_grocery_provider(legacy)
    if sticky and set(decision.providers) != {sticky} and _is_cart_op(message):
        return _routed(decision, [sticky], f"pinned to searched grocery {sticky}")
    return decision
