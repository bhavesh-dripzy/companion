"""Pure data contracts for the core pipeline — no I/O, no LLM, importable anywhere."""

from core.contracts.attachment import Attachment
from core.contracts.profile import Profile, SavedAddress
from core.contracts.routing import RoutingDecision, TaskType
from core.contracts.session import ProviderSessionState, SessionState
from core.contracts.tool import AuthMode, ConfirmPolicy, Tool
from core.contracts.tool_call import PlanConstraints, ToolCall, ToolPlan
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.contracts.turn import TurnRequest, TurnResponse
from core.contracts.turn_event import TurnEvent

__all__ = [
    "Attachment",
    "AuthMode",
    "ConfirmPolicy",
    "ErrorCode",
    "PlanConstraints",
    "Profile",
    "ProviderResult",
    "ProviderSessionState",
    "RoutingDecision",
    "SavedAddress",
    "SessionState",
    "TaskType",
    "Tool",
    "ToolCall",
    "ToolPlan",
    "ToolResult",
    "TurnEvent",
    "TurnRequest",
    "TurnResponse",
]
