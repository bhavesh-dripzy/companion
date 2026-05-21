"""Agent entry: orchestrator → domain food planner → MCP → response LLM."""

from __future__ import annotations

from typing import Any

from app.services.agents.orchestrator import classify_agent_routing
from app.services.agents.tool_trace import build_tool_execution_trace
from app.services.food_llm_router import route_food_query
from app.services.food_response_builder import (
    build_assistant_message_with_llm,
    build_zomato_summary,
)
from app.services.calendar import (
    execute_reminder_calendar,
    plan_reminder_calendar,
    reply_reminder_calendar,
)
from app.services.general_chat import plan_normal_query
from app.services.general_chat import responder as general_chat_responder
from app.services.llm_config import ResolvedLlm
from app.services.maps import (
    execute_maps_query,
    plan_maps_query,
    reply_maps_query,
)
from app.services.tool_runtime import execute_tool_plan_sync, get_tool_provider


class CustomMcpNeedsDiscoveryError(ValueError):
    """Raised when a routed custom MCP lacks a cached tool manifest."""


def _format_routing_message(
    current_message: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Build planner/orchestrator input: recent transcript + current user turn."""
    current = (current_message or "").strip()
    if not conversation_history:
        return current
    recent = [
        item
        for item in conversation_history[-8:]
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and item.get("content")
    ]
    if not recent:
        return current
    transcript = "\n".join(
        f"{item.get('role')}: {str(item.get('content') or '')[:700]}"
        for item in recent
    )
    return f"Conversation so far:\n{transcript}\n\nCurrent user message:\n{current}"


def _ensure_custom_providers_ready(routed_providers: set[str], user_id: str) -> None:
    for pid in sorted(routed_providers):
        tp = get_tool_provider(pid, user_id=user_id)
        if tp and tp.custom and not tp.tools:
            raise CustomMcpNeedsDiscoveryError(
                "Test this custom MCP first so Companion can discover its tools."
            )


def _empty_constraints_template() -> dict[str, Any]:
    return {
        "requires_address_resolution": False,
        "requested_location_text": None,
        "inferred_keyword": None,
        "price_max_inr": None,
        "price_min_inr": None,
        "min_rating": None,
        "near_and_fast": None,
        "vegetarian_only": None,
        "party_size_estimate": None,
        "health_constraints": None,
    }


def _non_food_plan(*, reasoning: str) -> dict[str, Any]:
    return {
        "intent": "unsupported_task",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints_template(),
        "execution_notes_for_downstream": "Task type is not wired to MCP tools yet.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _empty_food_plan(*, reasoning: str) -> dict[str, Any]:
    return {
        "intent": "clarify_request",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": _empty_constraints_template(),
        "execution_notes_for_downstream": "No food MCP providers selected; respond conversationally.",
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _empty_provider_plan(*, reasoning: str, task_type: str) -> dict[str, Any]:
    return {
        "intent": "connect_or_discover_provider",
        "summary": reasoning,
        "tool_plan": [],
        "constraints": {},
        "execution_notes_for_downstream": (
            f"{task_type} provider has no cached tool schema yet."
        ),
        "validation": {
            "tool_count": 0,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "system",
        },
    }


def _merge_provider_food_plans(
    labeled: list[tuple[str, dict[str, Any]]],
    *,
    resolved_model: str,
    routing_provider: str,
) -> dict[str, Any]:
    merged_steps: list[dict[str, Any]] = []
    summaries: list[str] = []
    intents: list[str] = []
    merged_constraints: dict[str, Any] = {}

    for pid, partial in labeled:
        st = str(partial.get("summary") or "").strip()
        if st:
            summaries.append(st)
        it = str(partial.get("intent") or "").strip()
        if it:
            intents.append(it)
        raw_c = partial.get("constraints")
        if isinstance(raw_c, dict):
            for key, val in raw_c.items():
                if key not in merged_constraints:
                    merged_constraints[key] = val
                elif key == "requires_address_resolution":
                    merged_constraints[key] = bool(merged_constraints.get(key)) or bool(val)
                elif merged_constraints.get(key) in (None, "", []) and val not in (None, "", []):
                    merged_constraints[key] = val

        tpl = partial.get("tool_plan")
        if not isinstance(tpl, list):
            continue
        for step in tpl:
            if not isinstance(step, dict):
                continue
            step_out = dict(step)
            if not str(step_out.get("provider") or "").strip():
                step_out["provider"] = pid
            merged_steps.append(step_out)

    if not merged_constraints:
        merged_constraints = _empty_constraints_template()

    summary_joined = " · ".join(summaries) if summaries else "Coordinated requests across providers."
    unique_intents = {i for i in intents if i}
    intent_pick = (
        next(iter(unique_intents)) if len(unique_intents) == 1 else "multi_provider_food_request"
    )

    return {
        "intent": intent_pick,
        "summary": summary_joined,
        "tool_plan": merged_steps,
        "constraints": merged_constraints,
        "execution_notes_for_downstream": (
            "Merged independent per-provider MCP plans; execute in parallel when possible."
        ),
        "validation": {
            "tool_count": len(merged_steps),
            "dropped_unknown_tool_steps": 0,
            "resolved_model": resolved_model,
            "completion_id": "",
            "provider": routing_provider,
        },
    }


def _plan_for_provider(
    provider_id: str,
    *,
    routing_message: str,
    llm: ResolvedLlm,
    user_id: str,
) -> dict[str, Any]:
    """Delegate to the provider's domain agent (LLM + prompt → tool_plan)."""
    tp = get_tool_provider(provider_id, user_id=user_id)

    if provider_id == "swiggy_food":
        from app.services.swiggy.planner import plan_swiggy_food

        return plan_swiggy_food(user_message=routing_message, llm=llm)
    if provider_id == "swiggy_instamart":
        from app.services.instamart.planner import plan_instamart

        return plan_instamart(user_message=routing_message, llm=llm)
    if provider_id == "zomato":
        from app.services.zomato.planner import plan_zomato_food

        return plan_zomato_food(user_message=routing_message, llm=llm)

    if tp and not tp.tools:
        return _empty_provider_plan(
            reasoning=(
                f"{tp.label} needs account authorization and tool discovery before "
                "Companion can plan calls for it. Use MCP Connect/Test first."
            ),
            task_type="finance_investing",
        )

    return route_food_query(
        user_message=routing_message,
        api_key=llm.api_key,
        model=llm.model,
        provider=llm.provider,
        base_url=llm.base_url,
        tool_provider=provider_id,
        tool_provider_label=tp.label if tp else None,
        tool_provider_tools=(sorted(tp.tools) if tp and tp.tools else None),
    )


def _resolve_food_routing_plan(
    *,
    routing_message: str,
    routed_providers: set[str],
    llm: ResolvedLlm,
    user_id: str,
) -> dict[str, Any]:
    if not routed_providers:
        return _empty_food_plan(
            reasoning="No food MCP providers remain after orchestration.",
        )

    if len(routed_providers) == 1:
        pid = next(iter(routed_providers))
        return _plan_for_provider(
            pid,
            routing_message=routing_message,
            llm=llm,
            user_id=user_id,
        )

    labeled: list[tuple[str, dict[str, Any]]] = []
    for pid in sorted(routed_providers):
        labeled.append(
            (
                pid,
                _plan_for_provider(
                    pid,
                    routing_message=routing_message,
                    llm=llm,
                    user_id=user_id,
                ),
            )
        )

    return _merge_provider_food_plans(
        labeled,
        resolved_model=llm.model,
        routing_provider=llm.provider,
    )


def run_agent_turn(
    *,
    user_message: str,
    address_id: str,
    execute_tools: bool,
    user_id: str,
    llm: ResolvedLlm,
    conversation_history: list[dict[str, str]] | None = None,
    user_geo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Orchestrated agent turn producing the same response envelope as legacy /api/food/query/.

    Flow: orchestrator LLM → per-provider domain LLM (tool_plan) → MCP executor → response LLM.
    """
    routing_message = _format_routing_message(user_message, conversation_history)
    orchestration = classify_agent_routing(user_message=routing_message, llm=llm)

    task_type = str(orchestration.get("task_type") or "food_delivery")
    if task_type == "finance_investing":
        routed = set(orchestration.get("finance_providers") or [])
    elif task_type == "health_nutrition":
        routed = set(orchestration.get("health_providers") or [])
    else:
        routed = set(orchestration.get("food_providers") or [])
    routed = {
        pid
        for pid in routed
        if isinstance(pid, str) and pid.strip() and pid.strip() != "blinkit"
    }

    if task_type == "whatsapp_message":
        from app.services.whatsapp.planner import plan_whatsapp_message
        from app.services.whatsapp.client import WhatsAppClient
        
        plan = plan_whatsapp_message(user_message=routing_message, user_id=user_id, llm=llm)
        whatsapp_result = None
        if execute_tools and plan.get("intent") == "send_whatsapp":
            client = WhatsAppClient()
            whatsapp_result = client.send_text_message(
                to=plan["recipient_phone"],
                text=plan["message"]
            )
        
        # Build a response message
        if plan.get("intent") == "send_whatsapp":
            if whatsapp_result and "error" not in whatsapp_result:
                assistant_message = f"Sent WhatsApp to {plan['recipient_name']}: \"{plan['message']}\""
            elif execute_tools:
                assistant_message = f"Failed to send WhatsApp to {plan['recipient_name']}. Please check credentials."
            else:
                assistant_message = f"I'll send a WhatsApp to {plan['recipient_name']} with the message: \"{plan['message']}\". (Execution skipped)"
        else:
            assistant_message = plan.get("reasoning") or "I couldn't figure out who to send that WhatsApp message to."

        return {
            "status": "success",
            "provider": llm.provider,
            "intent_routing": orchestration,
            "whatsapp_plan": plan,
            "whatsapp_result": whatsapp_result,
            "assistant_message": assistant_message,
        }

    if task_type == "normal_query":
        routed = set()
        plan = plan_normal_query(reasoning=str(orchestration.get("reasoning") or ""))
    elif task_type == "reminder_calendar":
        routed = set()
        plan = plan_reminder_calendar(
            reasoning=str(orchestration.get("reasoning") or ""),
            user_message=user_message,
        )
    elif task_type == "maps_query":
        routed = set()
        plan = plan_maps_query(
            reasoning=str(orchestration.get("reasoning") or ""),
            user_message=user_message,
            llm=llm,
        )
    elif task_type in {"finance_investing", "health_nutrition"}:
        if not routed:
            plan = _empty_provider_plan(
                reasoning=str(orchestration.get("reasoning") or "No MCP provider selected."),
                task_type=task_type,
            )
        else:
            _ensure_custom_providers_ready(routed, user_id)
            plan = _resolve_food_routing_plan(
                routing_message=routing_message,
                routed_providers=routed,
                llm=llm,
                user_id=user_id,
            )
    elif task_type != "food_delivery":
        plan = _non_food_plan(reasoning=str(orchestration.get("reasoning") or task_type))
    elif not routed:
        plan = _empty_food_plan(reasoning=str(orchestration.get("reasoning") or ""))
    else:
        _ensure_custom_providers_ready(routed, user_id)
        plan = _resolve_food_routing_plan(
            routing_message=routing_message,
            routed_providers=routed,
            llm=llm,
            user_id=user_id,
        )

    tool_execution: dict[str, Any] | None = None
    calendar_execution: dict[str, Any] | None = None
    maps_execution: dict[str, Any] | None = None
    provider_summaries: dict[str, Any] = {}
    zomato_summary = build_zomato_summary(None)
    raw_tool_results: dict[str, Any] = {}

    if task_type == "reminder_calendar" and execute_tools:
        calendar_execution = execute_reminder_calendar(
            user_message=user_message,
            user_id=user_id,
        )
    elif task_type == "maps_query" and execute_tools:
        maps_execution = execute_maps_query(
            plan=plan,
            user_geo=user_geo,
        )
    elif execute_tools:
        tool_plan = plan.get("tool_plan") if isinstance(plan, dict) else []
        enabled_providers = routed or None
        tool_execution = execute_tool_plan_sync(
            tool_plan if isinstance(tool_plan, list) else [],
            address_id=address_id,
            constraints=plan.get("constraints") if isinstance(plan, dict) else {},
            user_message=user_message,
            enabled_providers=enabled_providers,
            user_id=user_id,
        )
        provider_results = tool_execution.get("provider_results")
        if isinstance(provider_results, dict):
            for provider_name, raw in provider_results.items():
                if isinstance(raw, dict):
                    raw_tool_results[provider_name] = raw
                    provider_summaries[provider_name] = build_zomato_summary(raw)
            zraw = provider_results.get("zomato")
            zomato_summary = build_zomato_summary(zraw if isinstance(zraw, dict) else None)

    domain_execution: dict[str, Any] | None = (
        calendar_execution
        if task_type == "reminder_calendar"
        else maps_execution
        if task_type == "maps_query"
        else None
    )

    out: dict[str, Any] = {
        "status": "success",
        "provider": llm.provider,
        "intent_routing": orchestration,
        "selected_tool_providers": sorted(routed),
        "food_routing_plan": plan,
        "tool_execution": {
            "requested": bool(execute_tools),
            "routed_providers": (
                domain_execution.get("routed_providers", [])
                if isinstance(domain_execution, dict)
                else (
                    tool_execution.get("routed_providers", [])
                    if isinstance(tool_execution, dict)
                    else []
                )
            ),
            "ok": (
                domain_execution.get("ok")
                if isinstance(domain_execution, dict)
                else (tool_execution.get("ok") if isinstance(tool_execution, dict) else False)
            ),
            "error": (
                domain_execution.get("error")
                if isinstance(domain_execution, dict)
                else (tool_execution.get("error") if isinstance(tool_execution, dict) else None)
            ),
            "error_code": (
                domain_execution.get("error_code")
                if isinstance(domain_execution, dict)
                else None
            ),
        },
        "calendar_execution": calendar_execution,
        "maps_execution": maps_execution,
        "observable_trace": build_tool_execution_trace(
            plan=plan if isinstance(plan, dict) else {},
            selected_providers=routed,
            tool_execution=(
                tool_execution
                if execute_tools and task_type not in {"reminder_calendar", "maps_query"}
                else None
            ),
            intent_routing=orchestration,
            calendar_execution=calendar_execution if execute_tools else None,
        ),
        "zomato_summary": zomato_summary,
        "provider_summaries": provider_summaries,
        "assistant_message": (
            general_chat_responder.reply_general_chat(
                user_message=user_message,
                llm=llm,
                conversation_history=conversation_history,
            )
            if task_type == "normal_query"
            else reply_reminder_calendar(
                user_message=user_message,
                execution=calendar_execution,
                execute_tools=execute_tools,
            )
            if task_type == "reminder_calendar"
            else reply_maps_query(
                user_message=user_message,
                execution=maps_execution,
                execute_tools=execute_tools,
            )
            if task_type == "maps_query"
            else build_assistant_message_with_llm(
                user_message=user_message,
                plan=plan,
                tool_results=raw_tool_results,
                provider=llm.provider,
                api_key=llm.api_key,
                model=llm.model,
                base_url=llm.base_url,
                conversation_history=conversation_history,
            )
        ),
    }
    return out
