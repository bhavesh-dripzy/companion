"""Shared per-call execution loop for providers.

Both `McpProvider` and `HttpProvider` use this so the per-call loop — ordered
execution, placeholder flow between steps, and per-call error isolation — lives
in one tested place. The provider supplies an `execute_one` closure (for MCP,
bound to its already-open session).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult, ToolResult
from core.middleware.errors import result_from_exception
from core.providers.base import ExecCtx

ExecuteOne = Callable[[ToolCall], Awaitable[ToolResult]]


async def run_calls(
    execute_one: ExecuteOne,
    *,
    calls: list[ToolCall],
    provider_id: str,
    ctx: ExecCtx,
) -> ProviderResult:
    """Run calls in order; isolate failures; flow learned placeholders forward."""
    results: list[ToolResult] = []
    for call in calls:
        try:
            result = await execute_one(call)
        except Exception as exc:  # one call's failure must not abort the rest
            result = result_from_exception(call.tool, provider_id, exc, arguments=call.arguments)
        results.append(result)
        if result.learned_placeholders:
            ctx.placeholders.update(result.learned_placeholders)
    return ProviderResult(
        provider=provider_id,
        results=results,
        ok=all(not r.failed for r in results),
    )
