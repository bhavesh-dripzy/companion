"""`HttpProvider` — the shared base for statically-declared (non-MCP) providers.

For Twilio / Uber / Maps / Calendar: tools are declared in code (no runtime
discovery), and each is handled by a Python method. Stateless by default
(`session_cls = None`), which is exactly why Twilio no longer has to pretend to
be food-shaped (ADR-0002).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.session import ProviderSessionState
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.base import ExecCtx
from core.providers.runner import run_calls


class HttpProvider(ABC):
    id: str = ""
    label: str = ""
    auth_mode: AuthMode = AuthMode.NONE
    session_cls: type[ProviderSessionState] | None = None

    @abstractmethod
    def declared_tools(self) -> list[Tool]:
        """The provider's statically-declared tools (with real JSON-Schema params)."""

    @abstractmethod
    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        """Handle one tool call (an HTTP/SDK request)."""

    async def discover_tools(self) -> list[Tool]:
        return self.declared_tools()

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        async def _one(call: ToolCall) -> ToolResult:
            return await self.execute_one(call, ctx)

        return await run_calls(_one, calls=calls, provider_id=self.id, ctx=ctx)

    def build_attachments(self, result: ProviderResult) -> list[Attachment]:
        return []

    def merge(self, slice_: dict[str, Any], result: ProviderResult) -> dict[str, Any]:
        return slice_
