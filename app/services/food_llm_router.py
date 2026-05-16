"""
Shared LLM helpers for food domain agents.

Each provider agent (e.g. ``swiggy.planner``) loads its own prompt, calls
``complete_json_object``, then ``finalize_tool_plan`` to validate tool names only.
No deterministic shortcuts, fallbacks, or injected tool steps.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from openai import OpenAI

logger = logging.getLogger(__name__)

Provider = Literal["openai", "gemini", "deepseek"]

_SVC_ROOT = Path(__file__).resolve().parent

_BUILTIN_PROMPTS: dict[str, Path] = {
    "zomato": _SVC_ROOT / "zomato" / "prompts" / "food.system.md",
    "swiggy_food": _SVC_ROOT / "swiggy" / "prompts" / "food.system.md",
    "swiggy_instamart": _SVC_ROOT / "swiggy" / "prompts" / "instamart.system.md",
    "zepto": _SVC_ROOT / "zepto" / "prompts" / "food.system.md",
}

# Union of known MCP tool names (used only to drop hallucinated tools).
_ALLOWED_TOOLS = frozenset(
    {
        "get_saved_addresses_for_user",
        "get_restaurants_for_keyword",
        "get_restaurant_menu_by_categories",
        "get_menu_items_listing",
        "create_cart",
        "get_cart_offers",
        "checkout_cart",
        "get_order_history",
        "get_order_tracking_info",
        "bind_user_number",
        "bind_user_number_verify_code",
        "search_restaurants",
        "get_restaurant_menu",
        "search_menu",
        "search_menu_items",
        "get_addresses",
        "get_user_addresses",
        "get_cart",
        "get_food_cart",
        "update_food_cart",
        "flush_food_cart",
        "add_item_to_cart",
        "update_cart_item",
        "remove_cart_item",
        "clear_cart",
        "search_products",
        "get_product_details",
        "place_order",
        "place_food_order",
        "checkout",
        "get_order_details",
        "get_food_orders",
        "get_food_order_details",
        "get_orders",
        "track_food_order",
        "track_order",
        "fetch_food_coupons",
        "apply_food_coupon",
        "your_go_to_items",
        "update_cart",
        "report_error",
    }
)

_TOOL_ALIASES: dict[str, str] = {
    "get_user_addresses": "get_addresses",
    "search_menu_items": "search_menu",
}


@lru_cache(maxsize=8)
def _load_builtin_prompt(provider_key: str) -> str:
    path = _BUILTIN_PROMPTS.get(provider_key) or _BUILTIN_PROMPTS["zomato"]
    return path.read_text(encoding="utf-8").strip()


def router_prompt_fallback() -> str:
    return _load_builtin_prompt("zomato")


def _system_prompt_for_tool_provider(
    tool_provider: str | None,
    *,
    tool_provider_label: str | None = None,
    tool_provider_tools: list[str] | None = None,
) -> str:
    if tool_provider_tools:
        tools = "\n".join(f"- {tool}" for tool in tool_provider_tools)
        label = tool_provider_label or tool_provider or "Custom MCP"
        return f"""\
You translate the user's message into a machine-ready plan for {label}.

Respond with ONLY a single JSON object shaped exactly like this:
{{
  "intent": "<short snake_case intent label>",
  "summary": "<one sentence what the user wants>",
  "tool_plan": [
    {{"tool": "<exact tool name>", "arguments": {{}}}}
  ],
  "constraints": {{"requires_address_resolution": false, "requested_location_text": null, "inferred_keyword": null}},
  "execution_notes_for_downstream": "<optional hints>"
}}

TOOLS (use ONLY these exact tool strings):
{tools}

RULES:
1. Use the smallest useful chain of tools.
2. Never invent opaque IDs. Use placeholders such as "__ADDRESS_ID__" or "__RESTAURANT_ID__" only when a prior step supplies them.
3. Do not call destructive or purchasing tools unless the user explicitly confirms that action.
4. Return ONLY valid JSON.
"""
    pid = (tool_provider or "zomato").strip()
    if pid in _BUILTIN_PROMPTS:
        return _load_builtin_prompt(pid)
    return _load_builtin_prompt("zomato")


def normalize_tool_plan(
    tool_plan: list[dict[str, Any]],
    *,
    allowed_tools: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Keep LLM arguments as-is; only alias tool names and drop unknown tools."""
    allowed = allowed_tools or _ALLOWED_TOOLS
    out: list[dict[str, Any]] = []

    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        tool = step.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            continue
        tool = _TOOL_ALIASES.get(tool.strip(), tool.strip())
        if tool not in allowed:
            continue
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        normalized_step: dict[str, Any] = {
            "tool": tool,
            "arguments": dict(arguments),
        }
        provider_hint = step.get("provider")
        if isinstance(provider_hint, str) and provider_hint.strip():
            normalized_step["provider"] = provider_hint.strip()
        out.append(normalized_step)

    return out


def finalize_tool_plan(
    data: dict[str, Any],
    *,
    provider: Provider,
    model: str,
    trace_id: str,
    allowed_tools: set[str] | None = None,
) -> dict[str, Any]:
    tool_plan_raw = data.get("tool_plan")
    tool_plan = normalize_tool_plan(
        tool_plan_raw if isinstance(tool_plan_raw, list) else [],
        allowed_tools=allowed_tools,
    )

    constraints = data.get("constraints")
    if constraints is None or not isinstance(constraints, dict):
        constraints = {}
    constraints.setdefault("requested_location_text", None)

    normalized: dict[str, Any] = {
        "intent": str(data.get("intent", "") or "unknown"),
        "summary": str(data.get("summary", "") or ""),
        "tool_plan": tool_plan,
        "constraints": constraints,
        "execution_notes_for_downstream": str(
            data.get("execution_notes_for_downstream", "") or ""
        ),
    }

    raw_len = (
        sum(1 for s in tool_plan_raw if isinstance(s, dict))
        if isinstance(tool_plan_raw, list)
        else 0
    )

    normalized["validation"] = {
        "tool_count": len(tool_plan),
        "dropped_unknown_tool_steps": max(0, raw_len - len(tool_plan)),
        "resolved_model": model,
        "completion_id": trace_id,
        "provider": provider,
    }

    return normalized


# Backward-compatible alias for tests and legacy imports.
_finalize_from_parsed_json = finalize_tool_plan


def _json_from_llm(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("food_llm_router: invalid JSON from model: %s", e)
        raise ValueError("Model returned invalid JSON.") from e

    if not isinstance(data, dict):
        raise ValueError("Model payload must be a JSON object.")

    return data


def _openai_compatible_call(
    *,
    api_key: str,
    model: str,
    user_message: str,
    base_url: str | None = None,
    system_prompt: str | None = None,
) -> tuple[str, str | None]:
    resolved_system_prompt = system_prompt or router_prompt_fallback()
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    completion = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": resolved_system_prompt},
            {"role": "user", "content": user_message.strip()},
        ],
        response_format={"type": "json_object"},
    )

    content = completion.choices[0].message.content or "{}"
    tid = getattr(completion, "id", None) or ""
    return content, tid


def complete_json_object(
    *,
    user_message: str,
    system_prompt: str,
    api_key: str,
    model: str,
    provider: Provider,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Single-turn JSON from the model (orchestrator and domain agents)."""
    if provider in ("openai", "deepseek"):
        content, _tid = _openai_compatible_call(
            api_key=api_key,
            model=model,
            user_message=user_message,
            base_url=base_url if provider == "deepseek" else None,
            system_prompt=system_prompt,
        )
        return _json_from_llm(content)

    try:
        from google import genai as google_genai
    except ImportError as e:
        raise RuntimeError(
            "Gemini provider requires the google-genai package: pip install google-genai"
        ) from e

    client = google_genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=f"""
SYSTEM:
{system_prompt}

USER:
{user_message.strip()}
""",
    )
    text = response.text or "{}"
    return _json_from_llm(text)


def route_food_query(
    *,
    user_message: str,
    api_key: str,
    model: str,
    provider: Provider,
    base_url: str | None = None,
    tool_provider: str | None = None,
    tool_provider_label: str | None = None,
    tool_provider_tools: list[str] | None = None,
    skip_aggregate_shortcuts: bool = False,  # noqa: ARG001 — kept for call-site compatibility
    system_prompt_override: str | None = None,
    tool_providers: set[str] | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """Generic food domain agent (prompt + LLM → tool_plan). Prefer provider-specific planners when available."""
    allowed_tools = (
        {tool for tool in tool_provider_tools if isinstance(tool, str) and tool.strip()}
        if tool_provider_tools
        else None
    )
    override = (
        system_prompt_override.strip()
        if isinstance(system_prompt_override, str) and system_prompt_override.strip()
        else None
    )
    system_prompt = override or _system_prompt_for_tool_provider(
        tool_provider,
        tool_provider_label=tool_provider_label,
        tool_provider_tools=sorted(allowed_tools) if allowed_tools else None,
    )

    if provider in ("openai", "deepseek"):
        content, tid = _openai_compatible_call(
            api_key=api_key,
            model=model,
            user_message=user_message,
            base_url=base_url if provider == "deepseek" else None,
            system_prompt=system_prompt,
        )
        data = _json_from_llm(content)
        return finalize_tool_plan(
            data,
            provider=provider,
            model=model,
            trace_id=tid or "",
            allowed_tools=allowed_tools,
        )

    try:
        from google import genai as google_genai
    except ImportError as e:
        raise RuntimeError(
            "Gemini provider requires the google-genai package: pip install google-genai"
        ) from e

    client = google_genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=f"""
SYSTEM:
{system_prompt}

USER:
{user_message.strip()}
""",
    )
    text = response.text or "{}"
    data = _json_from_llm(text)
    return finalize_tool_plan(
        data,
        provider="gemini",
        model=model,
        trace_id="",
        allowed_tools=allowed_tools,
    )
