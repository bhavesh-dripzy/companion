"""High-level routing model: classify messages into downstream domain agents."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from app.services.food_llm_router import Provider as RouterProvider
from app.services.food_llm_router import complete_json_object
from app.services.llm_config import ResolvedLlm

_KNOWN_TASKS = frozenset(
    {
        "food_delivery",
        "finance_investing",
        "health_nutrition",
        "normal_query",
        "reminder_calendar",
        "whatsapp_message",
    }
)
_KNOWN_FOOD = frozenset({"zomato", "swiggy_food", "swiggy_instamart", "zepto"})
_KNOWN_FINANCE = frozenset({"kite", "groww"})
_KNOWN_HEALTH = frozenset({"daily_calorie_tracker"})


@lru_cache(maxsize=1)
def _load_orchestrator_prompt() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "orchestrator.system.md"
    return path.read_text(encoding="utf-8").strip()


def _previous_provider_fallback(previous_provider: str | None) -> tuple[str, list[str]]:
    """Map a previous_provider hint to (task_type, provider_list) for safe fallback.

    Used when the orchestrator LLM fails to return parseable JSON — instead of
    silently swapping to `swiggy_food` (and breaking address resolution for any
    user mid-flow on Zomato/Instamart), stick with the provider the previous
    successful turn used.
    """
    if previous_provider in _KNOWN_FOOD:
        return "food_delivery", [previous_provider]
    if previous_provider in _KNOWN_FINANCE:
        return "finance_investing", [previous_provider]
    if previous_provider in _KNOWN_HEALTH:
        return "health_nutrition", [previous_provider]
    return "food_delivery", ["zomato"]


def classify_agent_routing(
    *,
    user_message: str,
    llm: ResolvedLlm,
    previous_provider: str | None = None,
) -> dict[str, Any]:
    """
    Returns keys: task_type, food_providers, finance_providers, reasoning.

    ``previous_provider`` is an optional hint about which provider the previous
    successful turn used. It is appended to the routing message so the LLM can
    keep the user on the same provider mid-flow, and is also used as the safe
    fallback if the LLM returns malformed JSON (instead of the legacy hardcoded
    ``swiggy_food`` fallback which silently broke cross-provider sessions).
    """
    framed_message = user_message
    if previous_provider and isinstance(previous_provider, str):
        framed_message = (
            f"Previous successful turn used provider: {previous_provider}.\n"
            "Stay on this provider unless the user explicitly switches.\n\n"
            f"{user_message}"
        )

    raw: dict[str, Any]
    try:
        raw = complete_json_object(
            user_message=framed_message,
            system_prompt=_load_orchestrator_prompt(),
            api_key=llm.api_key,
            model=llm.model,
            provider=cast(RouterProvider, llm.provider),
            base_url=llm.base_url,
        )
    except ValueError:
        fb_task, fb_providers = _previous_provider_fallback(previous_provider)
        return {
            "task_type": fb_task,
            "food_providers": fb_providers if fb_task == "food_delivery" else [],
            "finance_providers": fb_providers if fb_task == "finance_investing" else [],
            "health_providers": fb_providers if fb_task == "health_nutrition" else [],
            "reasoning": (
                "Orchestrator returned non-JSON; "
                f"sticking with previous_provider={previous_provider or 'zomato (default)'}."
            ),
        }

    task = str(raw.get("task_type") or "food_delivery").strip()
    if task not in _KNOWN_TASKS:
        task = "food_delivery"

    foods_raw = raw.get("food_providers")
    if not isinstance(foods_raw, list):
        foods_raw = []
    foods = [
        str(x).strip()
        for x in foods_raw
        if isinstance(x, str) and str(x).strip() in _KNOWN_FOOD
    ]
    finance_raw = raw.get("finance_providers")
    if not isinstance(finance_raw, list):
        finance_raw = []
    finance = [
        str(x).strip()
        for x in finance_raw
        if isinstance(x, str) and str(x).strip() in _KNOWN_FINANCE
    ]
    health_raw = raw.get("health_providers")
    if not isinstance(health_raw, list):
        health_raw = []
    health = [
        str(x).strip()
        for x in health_raw
        if isinstance(x, str) and str(x).strip() in _KNOWN_HEALTH
    ]
    if task != "food_delivery":
        foods = []
    if task != "finance_investing":
        finance = []
    if task != "health_nutrition":
        health = []
    reasoning = str(raw.get("reasoning") or "").strip()

    return {
        "task_type": task,
        "food_providers": foods,
        "finance_providers": finance,
        "health_providers": health,
        "reasoning": reasoning or "(no reasoning given)",
    }
