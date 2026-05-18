"""Structured trace for MCP tool runs (also used by the HTTP handler)."""

from __future__ import annotations

from typing import Any


def build_tool_execution_trace(
    *,
    plan: dict[str, Any],
    selected_providers: set[str],
    tool_execution: dict[str, Any] | None,
    intent_routing: dict[str, Any] | None = None,
    calendar_execution: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []

    if intent_routing:
        ft = intent_routing.get("food_providers")
        foods_txt = ""
        if isinstance(ft, list) and ft:
            foods_txt = ", ".join(str(x) for x in ft if x)
        task_type = intent_routing.get("task_type")
        if task_type == "normal_query":
            detail = "Classifier chose general chat (normal_query); no food MCP."
        elif task_type == "reminder_calendar":
            detail = "Classifier chose calendar/reminder (reminder_calendar); Google Calendar path."
        else:
            detail = (
                f"Classifier chose task_type={task_type!r}; "
                f"food MCP providers=[{foods_txt or 'none'}]."
            )
        trace.append(
            {
                "stage": "intent_orchestration",
                "status": "ok",
                "detail": detail,
                "task_type": task_type,
                "food_providers": intent_routing.get("food_providers"),
            }
        )

    trace.extend(
        [
            {
                "stage": "provider_selection",
                "status": "ok",
                "detail": (
                    "Selected "
                    + (
                        ", ".join(sorted(selected_providers))
                        if selected_providers
                        else "no MCP provider"
                    )
                    + "."
                ),
                "providers": sorted(selected_providers),
            }
        ]
    )

    tool_plan = plan.get("tool_plan") if isinstance(plan, dict) else []
    if isinstance(tool_plan, list):
        planned_tools = [
            str(step.get("tool") or "")
            for step in tool_plan
            if isinstance(step, dict) and step.get("tool")
        ]
        trace.append(
            {
                "stage": "tool_plan",
                "status": "ok" if tool_plan else "skipped",
                "detail": (
                    f"Planned {len(tool_plan)} tool call(s)."
                    if tool_plan
                    else "No executable MCP tool call was planned."
                ),
                "tools": planned_tools,
            }
        )
    if isinstance(calendar_execution, dict):
        trace.append(
            {
                "stage": "calendar_execution",
                "provider": "google_calendar",
                "status": "ok" if calendar_execution.get("ok") else "error",
                "detail": (
                    "Google Calendar event created."
                    if calendar_execution.get("ok")
                    else str(calendar_execution.get("error") or "Calendar execution failed.")
                ),
                "tools": ["create_google_calendar_event"],
            }
        )
        trace.append(
            {
                "stage": "final_response",
                "status": "ok" if calendar_execution.get("ok") else "error",
                "detail": "Prepared response from calendar execution.",
            }
        )
        return trace

    if not isinstance(tool_execution, dict):
        trace.append(
            {
                "stage": "tool_execution",
                "status": "skipped",
                "detail": "Tool execution was not requested.",
            }
        )
        return trace

    provider_results = tool_execution.get("provider_results")
    if isinstance(provider_results, dict):
        for provider_name, result in sorted(provider_results.items()):
            status_value = "ok" if isinstance(result, dict) and result.get("ok") else "error"
            steps = result.get("steps") if isinstance(result, dict) else []
            trace.append(
                {
                    "stage": "provider_execution",
                    "provider": provider_name,
                    "status": status_value,
                    "detail": (
                        f"{provider_name} completed {len(steps) if isinstance(steps, list) else 0} step(s)."
                        if status_value == "ok"
                        else str(result.get("error_detail") or "Provider execution failed.")
                        if isinstance(result, dict)
                        else "Provider execution failed."
                    ),
                    "tools": [
                        str(step.get("tool") or "")
                        for step in steps
                        if isinstance(steps, list) and isinstance(step, dict) and step.get("tool")
                    ],
                }
            )
    elif tool_plan:
        trace.append(
            {
                "stage": "provider_execution",
                "status": "skipped",
                "detail": "No provider returned execution results.",
            }
        )

    trace.append(
        {
            "stage": "final_response",
            "status": "ok" if tool_execution.get("ok") else "error",
            "detail": "Prepared response from routing plan and tool results.",
        }
    )
    return trace
