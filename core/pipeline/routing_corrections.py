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
_GROCERY_PROVIDER_IDS = frozenset(pid for pid, _ in _GROCERY_CARTS)

# A food/grocery intent next to an app name — promotes a turn the orchestrator
# mis-classified as chit-chat (a named "search zepto for milk" routed to nobody).
_FOOD_ACTION_RE = re.compile(
    r"\b(search|find|show|get|order|reorder|buy|add|remove|delete|cart|checkout|"
    r"deliver|track|price|cheap|stock|menu|address|addresses|saved)\b"
)

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "my",
        "of",
        "to",
        "in",
        "and",
        "from",
        "for",
        "remove",
        "delete",
        "add",
        "cart",
        "please",
        "set",
        "change",
        "it",
        "that",
    }
)


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1
    }


def _routed(decision: RoutingDecision, providers: list[str], note: str) -> RoutingDecision:
    return RoutingDecision(
        task_type="food_delivery",
        providers=providers,
        reasoning=f"{decision.reasoning} ({note})",
        previous_provider=decision.previous_provider,
    )


def honor_named_providers(decision: RoutingDecision, message: str) -> RoutingDecision:
    """Route to the app(s) the user explicitly named.

    The orchestrator sometimes ignores an explicit name — "add milk to my instamart
    cart" misrouted to Zomato live — and intermittently classifies a named grocery
    search ("search zepto for milk") as chit-chat, routing it nowhere. When the
    message names a food/grocery app AND carries a food intent (search/cart/order/
    address…), promote the turn to that app even if the orchestrator didn't call it
    a food turn. A bare app mention with no food intent stays untouched (chit-chat)."""
    low = (message or "").lower()
    named: list[str] = []
    for name, pid in _NAMED_FOOD_PROVIDERS.items():
        if pid not in named and re.search(rf"\b{name}\b", low):
            named.append(pid)
    if not named:
        return decision
    if decision.task_type != "food_delivery":
        # Promote a non-food turn only for a GROCERY app + a clear food intent, so a
        # query like "Zomato stock price" (a listed company) isn't hijacked into a
        # restaurant search — grocery names don't collide with finance/news lookups.
        grocery = [p for p in named if p in _GROCERY_PROVIDER_IDS]
        if not grocery or not _FOOD_ACTION_RE.search(low):
            return decision
        named = grocery
    elif set(named) == set(decision.providers):
        return decision
    return _routed(decision, named, f"named {named}")


def active_cart_providers(legacy: dict[str, Any]) -> list[str]:
    """Grocery providers whose carts currently hold items (cart order preserved)."""
    if not isinstance(legacy, dict):
        return []
    out: list[str] = []
    for pid, key in _GROCERY_CARTS:
        cart = legacy.get(key)
        if isinstance(cart, dict) and cart.get("items"):
            out.append(pid)
    return out


def active_cart_provider(legacy: dict[str, Any]) -> str | None:
    """The first grocery provider whose cart holds items, or None (compat shim)."""
    active = active_cart_providers(legacy)
    return active[0] if active else None


def _cart_named_provider(message: str, legacy: dict[str, Any]) -> str | None:
    """The active cart holding an item the message names — unambiguous match only.

    Disambiguates the both-carts case: "remove the milk" with milk in Zepto and
    bread in Instamart must hit Zepto, not whichever cart is listed first."""
    want = _tokens(message)
    if not want:
        return None
    hits: list[str] = []
    for pid, key in _GROCERY_CARTS:
        cart = legacy.get(key)
        items = cart.get("items") if isinstance(cart, dict) else None
        for it in items or []:
            if isinstance(it, dict) and (_tokens(it.get("name", "")) & want):
                hits.append(pid)
                break
    return hits[0] if len(set(hits)) == 1 else None


def stick_to_active_cart(
    decision: RoutingDecision, message: str, legacy: dict[str, Any]
) -> RoutingDecision:
    """Pin a grocery turn to the provider holding the live cart.

    The orchestrator routes the same grocery conversation to Instamart, then Zepto,
    then both — so "remove the milk" / "what's in my cart" lands on whichever cart,
    and a stray write leaks to the wrong app. Once a grocery cart is active, route
    grocery turns to THAT provider — unless the user named one (then honour the
    name). When BOTH carts are active, disambiguate by the named item, then the
    provider used last turn; only fan out (read-only discovery) when undecidable."""
    if message_names_provider(message):
        return decision
    grocery = {pid for pid, _ in _GROCERY_CARTS}
    if not any(p in grocery for p in decision.providers):
        return decision
    active = active_cart_providers(legacy)
    if not active:
        return decision
    if len(active) == 1:
        pid = active[0]
        return (
            decision
            if set(decision.providers) == {pid}
            else _routed(decision, [pid], f"pinned to active {pid} cart")
        )
    pid = _cart_named_provider(message, legacy)
    if pid is None and decision.previous_provider in active:
        pid = decision.previous_provider
    if pid is None or set(decision.providers) == {pid}:
        return decision
    return _routed(decision, [pid], f"pinned to {pid} cart (both active)")
