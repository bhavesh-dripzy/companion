"""`McpProvider` — the shared base for every MCP-backed provider.

Implements `discover_tools` and `run` once: open ONE session per turn, then for
each call clear stale placeholders, substitute, guard unresolved placeholders,
call the tool, parse the envelope, and learn new placeholders. A concrete plugin
(Phase 4) supplies only `stdio_spec` (command/args/env incl. tokens) and its
`build_attachments` / `merge` — roughly 200 lines instead of a 3,500-line executor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.session import ProviderSessionState
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult, ToolResult
from core.middleware.errors import UnresolvedPlaceholderError
from core.providers.base import ExecCtx
from core.providers.mcp.connection import StdioSpec, call_tool, list_tools, mcp_session
from core.providers.mcp.placeholders import PlaceholderResolver
from core.providers.mcp.result_parse import tool_result_payload
from core.providers.runner import run_calls


class McpProvider(ABC):
    id: str = ""
    label: str = ""
    auth_mode: AuthMode = AuthMode.OAUTH
    session_cls: type[ProviderSessionState] | None = None
    resolver: PlaceholderResolver = PlaceholderResolver()

    @abstractmethod
    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        """Build the stdio launch spec (mcp-remote command + token env) for this turn."""

    async def before_run(self, session: Any, ctx: ExecCtx) -> None:
        """Hook run once after connect, before the first call. Seeds the address id;
        plugins may override to prefetch saved addresses, etc."""
        if ctx.address_id and "__ADDRESS_ID__" not in ctx.placeholders:
            ctx.placeholders["__ADDRESS_ID__"] = ctx.address_id

    async def discover_tools(self) -> list[Tool]:
        async with mcp_session(self.stdio_spec(ExecCtx())) as session:
            raw = await list_tools(session)
        return [
            Tool(
                name=item["name"],
                provider=self.id,
                description=item.get("description", ""),
                params=item.get("input_schema") or {},
                auth_mode=self.auth_mode,
                source="mcp",
            )
            for item in raw
        ]

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        async with mcp_session(self.stdio_spec(ctx)) as session:
            await self.before_run(session, ctx)

            async def execute_one(call: ToolCall) -> ToolResult:
                self.resolver.clear_stale(ctx.placeholders, call.tool)
                args = self.resolver.substitute(dict(call.arguments), ctx.placeholders)
                unresolved = self.resolver.unresolved(args)
                if unresolved:
                    raise UnresolvedPlaceholderError(
                        f"unresolved placeholders: {', '.join(unresolved)}"
                    )
                raw = await call_tool(session, call.tool, args)
                envelope = tool_result_payload(raw)
                learned = self.resolver.learn(ctx.placeholders, envelope)
                return ToolResult(
                    tool=call.tool,
                    provider=self.id,
                    ok=not bool(envelope.get("is_error")),
                    arguments=args,
                    data=envelope,
                    learned_placeholders=learned,
                )

            return await run_calls(execute_one, calls=calls, provider_id=self.id, ctx=ctx)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return []

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return slice_
