"""High-level routing model: classify message into downstream domains (currently food MCP)."""

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


def classify_agent_routing(*, user_message: str, llm: ResolvedLlm) -> dict[str, Any]:
    """
    Returns keys: task_type, food_providers, finance_providers, reasoning.
    """
    raw: dict[str, Any]
    try:
        raw = complete_json_object(
            user_message=user_message,
            system_prompt=_load_orchestrator_prompt(),
            api_key=llm.api_key,
            model=llm.model,
            provider=cast(RouterProvider, llm.provider),
            base_url=llm.base_url,
        )
    except ValueError:
        return {
            "task_type": "food_delivery",
            "food_providers": ["swiggy_food"],
            "finance_providers": [],
            "health_providers": [],
            "reasoning": (
                "Orchestrator returned non-JSON; defaulting to Swiggy Food for safety."
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
    reasoning = str(raw.get("reasoning") or "").strip()

    return {
        "task_type": task,
        "food_providers": foods,
        "finance_providers": finance,
        "health_providers": health,
        "reasoning": reasoning or "(no reasoning given)",
    }
