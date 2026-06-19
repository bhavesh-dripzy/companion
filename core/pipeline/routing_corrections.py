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


def stick_to_active_cart(
    decision: RoutingDecision, message: str, legacy: dict[str, Any]
) -> RoutingDecision:
    """Pin a grocery turn to the provider holding the live cart.

    The orchestrator routes the same grocery conversation to Instamart, then Zepto,
    then both — so "remove the milk" / "what's in my cart" lands on whichever cart,
    and a stray write leaks to the wrong app. Once a grocery cart is active, route
    grocery turns to THAT provider — unless the user named one (then honour the
    name). Discovery with no cart still fans out for price comparison."""
    if message_names_provider(message):
        return decision
    grocery = {pid for pid, _ in _GROCERY_CARTS}
    if not any(p in grocery for p in decision.providers):
        return decision
    pid = active_cart_provider(legacy)
    if not pid or set(decision.providers) == {pid}:
        return decision
    return _routed(decision, [pid], f"pinned to active {pid} cart")
