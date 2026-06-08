"""Validate a planner's raw JSON into a typed `ToolPlan`.

Preserves the legacy `normalize_tool_plan` behaviour (alias names, drop unknown
tools, keep arguments as-is) and adds opt-in JSON-Schema argument validation for
when plugins supply real `params` (Phase 4). Arg validation is off by default so
the core path matches the legacy name-only normalization exactly.
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool import Tool
from core.contracts.tool_call import PlanConstraints, ToolCall, ToolPlan
from core.registry.tool_registry import ToolRegistry

try:  # optional: only used when validate_args=True and a tool has a schema
    import jsonschema as _jsonschema
except Exception:  # pragma: no cover - optional dependency
    _jsonschema = None

# Placeholder arguments are legal pre-substitution, so skip schema checks for them.
_PLACEHOLDER_PREFIX = "__"


def _looks_like_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PLACEHOLDER_PREFIX) and value.endswith("__")


def _args_valid(tool: Tool, arguments: dict[str, Any]) -> bool:
    if _jsonschema is None or not tool.params:
        return True
    # Drop placeholder-valued keys before validating; they resolve at execution.
    concrete = {k: v for k, v in arguments.items() if not _looks_like_placeholder(v)}
    try:
        _jsonschema.validate(concrete, tool.params)
        return True
    except Exception:
        return False


def normalize_plan(
    raw: dict[str, Any],
    *,
    provider: str,
    tools: ToolRegistry,
    validate_args: bool = False,
) -> tuple[ToolPlan, int]:
    """Return (ToolPlan, dropped_count). Unknown/invalid tool steps are dropped."""
    raw_calls = raw.get("tool_plan") if isinstance(raw, dict) else None
    if not isinstance(raw_calls, list):
        raw_calls = []

    calls: list[ToolCall] = []
    dropped = 0
    for step in raw_calls:
        if not isinstance(step, dict):
            continue
        name = step.get("tool")
        if not isinstance(name, str) or not name.strip():
            continue
        tool = tools.get(name.strip(), provider=provider) or tools.get(name.strip())
        if tool is None:
            dropped += 1
            continue
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if validate_args and not _args_valid(tool, arguments):
            dropped += 1
            continue
        hint = step.get("provider")
        calls.append(
            ToolCall(
                tool=tool.name,  # canonical (aliases resolved)
                arguments=dict(arguments),
                provider=hint.strip() if isinstance(hint, str) and hint.strip() else None,
            )
        )

    constraints_raw = raw.get("constraints") if isinstance(raw, dict) else None
    constraints = (
        PlanConstraints.model_validate(constraints_raw)
        if isinstance(constraints_raw, dict)
        else PlanConstraints()
    )
    plan = ToolPlan(
        intent=str(raw.get("intent") or "unknown"),
        summary=str(raw.get("summary") or ""),
        calls=calls,
        constraints=constraints,
        notes=str(raw.get("execution_notes_for_downstream") or ""),
        provider=provider,
    )
    return plan, dropped
