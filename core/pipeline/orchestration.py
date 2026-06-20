"""Orchestrator parsing: legacy multi-list routing -> unified RoutingDecision.

Ports `agents.orchestrator.classify_agent_routing` validation and
`pipeline._extract_previous_provider` stickiness. Keeps the exact safety rules:
going-out is District-only; a malformed orchestrator response sticks with the
previous provider rather than silently defaulting (ADR-0002 / parity).
"""

from __future__ import annotations

import re
from typing import Any

from core.contracts.routing import RoutingDecision, TaskType

KNOWN_TASKS = frozenset(t.value for t in TaskType)
KNOWN_FOOD = frozenset({"zomato", "swiggy_food", "swiggy_instamart", "zepto", "district"})
KNOWN_FINANCE = frozenset({"kite", "groww"})
KNOWN_HEALTH = frozenset({"daily_calorie_tracker"})
KNOWN_DATA = frozenset({"serp"})
KNOWN_PROVIDER_IDS = KNOWN_FOOD | KNOWN_FINANCE | KNOWN_HEALTH | KNOWN_DATA
# Tasks served by exactly one (HTTP) provider. (cab_booking/uber removed — not live.)
_SOLE_PROVIDER = {
    "phone_call": "twilio",
    "maps_query": "maps",
    "reminder_calendar": "calendar",
    "whatsapp_message": "whatsapp",
    "data_lookup": "serp",
    "carpool": "blablacar",
}


def _str_list(raw: dict[str, Any], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if isinstance(x, str) and str(x).strip()]


def extract_previous_provider(history: list[dict[str, Any]] | None) -> str | None:
    """Best-effort provider stickiness from prior assistant turns (None if unsure)."""
    if not history:
        return None
    recent = [i for i in history[-6:] if isinstance(i, dict) and i.get("role") == "assistant"]
    for item in reversed(recent):
        pid = item.get("provider")
        if isinstance(pid, str) and pid.strip() in KNOWN_PROVIDER_IDS:
            return pid.strip()
    for item in reversed(recent):
        content = str(item.get("content") or "").lower()
        if not content:
            continue
        has_instamart = "instamart" in content
        if has_instamart:
            return "swiggy_instamart"
        if "zepto" in content:
            return "zepto"
        if "district" in content:
            return "district"
        if "zomato" in content:
            return "zomato"
        if "swiggy" in content:
            return "swiggy_food"
    return None


def frame_message(user_message: str, previous_provider: str | None) -> str:
    if not previous_provider:
        return user_message
    return (
        f"Previous successful turn used provider: {previous_provider}.\n"
        "Stay on this provider unless the user explicitly switches.\n\n"
        f"{user_message}"
    )


def _decision(task: str, providers: list[str], reason: str, prev: str | None) -> RoutingDecision:
    return RoutingDecision(
        task_type=task, providers=providers, reasoning=reason, previous_provider=prev
    )


def fallback_routing(previous_provider: str | None) -> RoutingDecision:
    """Used when the orchestrator returns non-JSON — stick with the prev provider."""
    reason = (
        f"orchestrator JSON error; sticking with previous_provider={previous_provider or 'zomato'}"
    )
    if previous_provider in KNOWN_FOOD:
        return _decision("food_delivery", [previous_provider], reason, previous_provider)
    if previous_provider in KNOWN_FINANCE:
        return _decision("finance_investing", [previous_provider], reason, previous_provider)
    if previous_provider in KNOWN_HEALTH:
        return _decision("health_nutrition", [previous_provider], reason, previous_provider)
    if previous_provider in KNOWN_DATA:
        return _decision("data_lookup", [previous_provider], reason, previous_provider)
    if previous_provider == "twilio":
        return _decision("phone_call", ["twilio"], reason, previous_provider)
    return _decision("food_delivery", ["zomato"], reason, previous_provider)


# #14 agnostic fan-out: per-domain provider groups. The orchestrator picks one
# seed provider (grocery -> swiggy_instamart, meals -> zomato); expansion fans
# the seed out to its whole group. District is dining-out, never expanded.
_AGNOSTIC_GROUPS = (
    ("zomato", "swiggy_food"),
    ("swiggy_instamart", "zepto"),
)

# Grocery providers always fan out on every generic discovery turn — both
# platforms are checked until the user explicitly names one. Food ordering
# uses stickiness instead (once mid-order on Zomato, stay there).
_ALWAYS_FANOUT: frozenset[str] = frozenset({"swiggy_instamart", "zepto"})
_PROVIDER_NAME_HINTS = (
    "zomato",
    "swiggy",
    "instamart",
    "zepto",
    "blinkit",
    "district",
    "kite",
    "zerodha",
)


def message_names_provider(message: str) -> bool:
    """True if the user explicitly named a provider/app in their message."""
    low = (message or "").lower()
    return any(hint in low for hint in _PROVIDER_NAME_HINTS)


_TRANSACTIONAL_RE = re.compile(
    r"\b(checkout|check out|pay|payment|confirm|cancel|track|reorder)\b|^my (cart|order)"
)


def message_is_transactional(message: str) -> bool:
    """True for cart/checkout/order-management turns — never fanned out (#14)."""
    return bool(_TRANSACTIONAL_RE.search((message or "").lower()))


def expand_agnostic(
    decision: RoutingDecision, message: str, *, enabled: bool, allowed: list[str] | None = None
) -> RoutingDecision:
    """Issue #14: name an app -> that app; name none -> fan out within the domain group.

    Only applies to discovery turns on food delivery; gated by `enabled`.
    Transactional turns (checkout/pay/cancel/track) and mid-flow turns (the
    orchestrator stuck with the previous provider) are never re-fanned, so a
    cart in progress stays on its provider. `allowed` (the client's enabled
    providers) intersects the fan-out when provided.
    """
    if not enabled or decision.task_type != "food_delivery" or message_names_provider(message):
        return decision
    if message_is_transactional(message):
        return decision
    # Grocery providers (Instamart + Zepto) always fanout on generic discovery —
    # skip stickiness so both are checked every turn until the user names one.
    # Food ordering (Zomato/Swiggy) keeps stickiness — stays on current provider mid-flow.
    in_always_fanout = any(p in _ALWAYS_FANOUT for p in decision.providers)
    previous_in_decision = (
        decision.previous_provider and decision.previous_provider in decision.providers
    )
    if not in_always_fanout and previous_in_decision:
        return decision
    providers: list[str] = []
    for group in _AGNOSTIC_GROUPS:
        if any(p in group for p in decision.providers):
            providers.extend(p for p in group if not allowed or p in allowed)
    if not providers or set(providers) == set(decision.providers):
        return decision
    return _decision(
        decision.task_type,
        providers,
        f"{decision.reasoning} (agnostic fan-out)",
        decision.previous_provider,
    )


def parse_routing(raw: dict[str, Any], previous_provider: str | None) -> RoutingDecision:
    """Validate the orchestrator response into a single task + provider list."""
    task = str(raw.get("task_type") or "food_delivery").strip()
    if task not in KNOWN_TASKS:
        task = "food_delivery"

    if task in ("food_delivery", "going_out"):
        foods = [p for p in _str_list(raw, "food_providers") if p in KNOWN_FOOD]
        if task == "going_out":
            foods = [p for p in foods if p == "district"] or ["district"]
        elif not foods:
            foods = [previous_provider] if previous_provider in KNOWN_FOOD else ["zomato"]
        providers = foods
    elif task == "finance_investing":
        providers = [p for p in _str_list(raw, "finance_providers") if p in KNOWN_FINANCE] or [
            "kite"
        ]
    elif task == "health_nutrition":
        providers = [p for p in _str_list(raw, "health_providers") if p in KNOWN_HEALTH] or [
            "daily_calorie_tracker"
        ]
    elif task in _SOLE_PROVIDER:
        providers = [_SOLE_PROVIDER[task]]
    else:  # normal_query — chit-chat, no tools
        providers = []

    reasoning = str(raw.get("reasoning") or "").strip() or "(no reasoning given)"
    return _decision(task, providers, reasoning, previous_provider)
