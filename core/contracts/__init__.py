"""Core data contracts retained for the MCP/OAuth layer.

The tool-calling / pipeline contracts (Tool, ToolCall, ToolPlan, SessionState,
Attachment, RoutingDecision, Turn*, Profile, …) were removed with the legacy
orchestrator. The new backend will define its own. What remains is the result
taxonomy the MCP error/repair path still uses.
"""

from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult

__all__ = [
    "ErrorCode",
    "ProviderResult",
    "ToolResult",
]
