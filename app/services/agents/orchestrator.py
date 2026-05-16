"""High-level routing model: classify message into downstream domains (currently food MCP)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from app.services.food_llm_router import Provider as RouterProvider
from app.services.food_llm_router import complete_json_object
from app.services.llm_config import ResolvedLlm

_KNOWN_TASKS = frozenset({"food_delivery", "reminder_calendar", "whatsapp_message"})
_KNOWN_FOOD = frozenset({"zomato", "swiggy_food", "swiggy_instamart", "zepto"})


@lru_cache(maxsize=1)
def _load_orchestrator_prompt() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "orchestrator.system.md"
    return path.read_text(encoding="utf-8").strip()


def classify_agent_routing(*, user_message: str, llm: ResolvedLlm) -> dict[str, Any]:
    """
    Returns keys: task_type, food_providers (list[str]), reasoning (str).
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
    reasoning = str(raw.get("reasoning") or "").strip()

    return {
        "task_type": task,
        "food_providers": foods,
        "reasoning": reasoning or "(no reasoning given)",
    }
