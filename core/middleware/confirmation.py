"""The confirmation gate.

A safety net that holds back mutating tools (carts/checkouts/orders) until the
user has confirmed — independent of the planner prompt also gating them. Kite's
real-money tools (`TWO_STEP`) and single-confirm purchases never run unconfirmed.
"""

from __future__ import annotations

from core.contracts.tool import ConfirmPolicy
from core.contracts.tool_call import ToolCall, ToolPlan
from core.registry.tool_registry import ToolRegistry


def policy_for(name: str, provider: str, tools: ToolRegistry) -> ConfirmPolicy:
    tool = tools.get(name, provider=provider) or tools.get(name)
    return tool.confirm if tool else ConfirmPolicy.NEVER


def partition(
    plan: ToolPlan,
    *,
    provider: str,
    tools: ToolRegistry,
    confirmed: bool,
) -> tuple[list[ToolCall], list[ToolCall]]:
    """Split a plan's calls into (allowed, blocked-pending-confirmation).

    A call is allowed when its tool needs no confirmation, or when the turn is a
    confirmation turn. Otherwise it is blocked and surfaced to the user.
    """
    allowed: list[ToolCall] = []
    blocked: list[ToolCall] = []
    for call in plan.calls:
        needs = policy_for(call.tool, provider, tools) is not ConfirmPolicy.NEVER
        (blocked if (needs and not confirmed) else allowed).append(call)
    return allowed, blocked
