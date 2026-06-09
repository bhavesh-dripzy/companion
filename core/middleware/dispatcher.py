"""Registry-driven async dispatch — the replacement for the if/elif tool router.

Groups a plan's calls by provider, then runs the providers concurrently
(`asyncio.gather`, ADR-0003). Each provider runs its whole group in one batch
(`provider.run`) so MCP opens a single session per turn. A provider that fails
entirely (e.g. connect/auth before any call) becomes typed failed results,
never aborting the other providers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult, ToolResult
from core.middleware.errors import result_from_exception
from core.providers.base import ExecCtx
from core.registry.provider_registry import UNSUPPORTED, ProviderRegistry

CtxFactory = Callable[[str], ExecCtx]


class Dispatcher:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def dispatch(self, calls: list[ToolCall], ctx_for: CtxFactory) -> list[ProviderResult]:
        """Fan out calls to their providers concurrently; collect per-provider results."""
        routed = self.registry.route(calls)
        runnable = {pid: group for pid, group in routed.items() if pid != UNSUPPORTED}
        results = list(
            await asyncio.gather(
                *(self._run_provider(pid, group, ctx_for) for pid, group in runnable.items())
            )
        )
        unsupported = routed.get(UNSUPPORTED)
        if unsupported:
            results.append(self._unsupported_result(unsupported))
        return results

    async def _run_provider(
        self, provider_id: str, calls: list[ToolCall], ctx_for: CtxFactory
    ) -> ProviderResult:
        provider = self.registry.get(provider_id)
        if provider is None:
            return self._unsupported_result(calls, provider_id)
        ctx = ctx_for(provider_id)
        try:
            return await provider.run(calls, ctx)
        except Exception as exc:  # whole-provider failure -> typed results, others unaffected
            results = [
                result_from_exception(call.tool, provider_id, exc, arguments=call.arguments)
                for call in calls
            ]
            return ProviderResult(provider=provider_id, results=results, ok=False)

    @staticmethod
    def _unsupported_result(
        calls: list[ToolCall], provider_id: str = UNSUPPORTED
    ) -> ProviderResult:
        results = [
            ToolResult(
                tool=call.tool,
                provider=provider_id,
                ok=False,
                error=None,
                error_detail="no provider owns this tool",
            )
            for call in calls
        ]
        return ProviderResult(provider=provider_id, results=results, ok=False)
