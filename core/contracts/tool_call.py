"""The planner's output: a validated plan of tool calls.

Mirrors the legacy `finalize_tool_plan` shape (intent / summary / tool_plan /
constraints / execution_notes_for_downstream) but as typed models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    """One planned tool invocation."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None  # optional provider hint from the planner


class PlanConstraints(BaseModel):
    """Planner-surfaced constraints (extra keys allowed for forward-compat)."""

    model_config = ConfigDict(extra="allow")

    requires_address_resolution: bool = False
    requested_location_text: str | None = None
    inferred_keyword: str | None = None


class ToolPlan(BaseModel):
    """A provider's plan for the turn."""

    intent: str = "unknown"
    summary: str = ""
    calls: list[ToolCall] = Field(default_factory=list)
    constraints: PlanConstraints = Field(default_factory=PlanConstraints)
    notes: str = ""  # legacy execution_notes_for_downstream
    provider: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.calls
