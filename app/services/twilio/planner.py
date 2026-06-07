"""Phone call planner — LLM generates a call tool_plan from natural language."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from app.services.food_llm_router import Provider as RouterProvider
from app.services.food_llm_router import complete_json_object
from app.services.llm_config import ResolvedLlm


@lru_cache(maxsize=1)
def _load_planner_prompt() -> str:
    path = Path(__file__).resolve().parent / "prompts" / "planner.system.md"
    return path.read_text(encoding="utf-8").strip()


def plan_phone_call(
    *,
    routing_message: str,
    llm: ResolvedLlm,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Parse a user message into a phone-call tool plan.

    The planner LLM extracts the destination phone number, generates a
    natural Hinglish/English spoken message, and emits a single-step
    ``tool_plan`` that the generic ``execute_tool_plan_sync`` pipeline
    can dispatch to the Twilio provider.

    Args:
        routing_message: The formatted routing message (user text + context).
        llm: Resolved LLM configuration.
        user_id: Optional user identifier (reserved for future profile hooks).

    Returns:
        A dict with ``intent``, ``tool_plan``, and ``constraints`` keys
        compatible with the generic ``_resolve_food_routing_plan`` flow.
    """
    system_prompt = _load_planner_prompt()

    try:
        raw = complete_json_object(
            user_message=routing_message,
            system_prompt=system_prompt,
            api_key=llm.api_key,
            model=llm.model,
            provider=cast(RouterProvider, llm.provider),
            base_url=llm.base_url,
        )
    except ValueError:
        return {
            "intent": "phone_call_error",
            "tool_plan": [],
            "constraints": {},
        }

    tool_plan = raw.get("tool_plan") if isinstance(raw.get("tool_plan"), list) else []
    return {
        "intent": "phone_call",
        "tool_plan": tool_plan,
        "constraints": raw.get("constraints") or {},
    }
