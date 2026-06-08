"""Orchestrator parsing: legacy multi-list routing -> unified RoutingDecision.

Ports `agents.orchestrator.classify_agent_routing` validation and
`pipeline._extract_previous_provider` stickiness. Keeps the exact safety rules:
going-out is District-only; a malformed orchestrator response sticks with the
previous provider rather than silently defaulting (ADR-0002 / parity).
"""

from __future__ import annotations

from typing import Any

from core.contracts.routing import RoutingDecision, TaskType

KNOWN_TASKS = frozenset(t.value for t in TaskType)
KNOWN_FOOD = frozenset({"zomato", "swiggy_food", "swiggy_instamart", "zepto", "district"})
KNOWN_FINANCE = frozenset({"kite", "groww"})
KNOWN_HEALTH = frozenset({"daily_calorie_tracker"})
KNOWN_PROVIDER_IDS = KNOWN_FOOD | KNOWN_FINANCE | KNOWN_HEALTH
# Tasks served by exactly one (HTTP) provider.
_SOLE_PROVIDER = {
    "cab_booking": "uber",
    "phone_call": "twilio",
    "maps_query": "maps",
    "reminder_calendar": "calendar",
    "whatsapp_message": "whatsapp",
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
    if previous_provider in ("uber", "twilio"):
        task = "cab_booking" if previous_provider == "uber" else "phone_call"
        return _decision(task, [previous_provider], reason, previous_provider)
    return _decision("food_delivery", ["zomato"], reason, previous_provider)


# Delivery food providers fanned out to when the user names no specific app (#14).
# (District is dining-out, not delivery, so it is excluded from delivery fan-out.)
_DELIVERY_FOOD = ("zomato", "swiggy_food", "swiggy_instamart", "zepto")
_PROVIDER_NAME_HINTS = (
    "zomato",
    "swiggy",
    "instamart",
    "zepto",
    "blinkit",
    "district",
    "kite",
    "zerodha",
    "uber",
)


def message_names_provider(message: str) -> bool:
    """True if the user explicitly named a provider/app in their message."""
    low = (message or "").lower()
    return any(hint in low for hint in _PROVIDER_NAME_HINTS)


def expand_agnostic(
    decision: RoutingDecision, message: str, *, enabled: bool, allowed: list[str] | None = None
) -> RoutingDecision:
    """Issue #14: name an app -> that app; name none -> fan out to all relevant apps.

    Only applies to food delivery; gated by `enabled`. `allowed` (the client's
    enabled providers) intersects the fan-out when provided.
    """
    if not enabled or decision.task_type != "food_delivery" or message_names_provider(message):
        return decision
    providers = [p for p in _DELIVERY_FOOD if not allowed or p in allowed]
    if not providers:
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
