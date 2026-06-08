"""The provider plugin interface.

Every provider — MCP or HTTP — implements this Protocol. `McpProvider` /
`HttpProvider` bases (Phase 2) supply most of it so concrete plugins stay small
(ADR-0002). The registry stores and routes to `Provider`s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.contracts.attachment import Attachment
from core.contracts.session import ProviderSessionState
from core.contracts.tool import AuthMode, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ToolResult
from core.llm.config import ResolvedLlm


@dataclass
class ExecCtx:
    """Per-call execution context handed to `Provider.execute`."""

    user_id: str | None = None
    address_id: str | None = None
    user_geo: dict[str, Any] | None = None
    # Cross-provider placeholders learned so far this turn (__CART_ID__, ...).
    placeholders: dict[str, str] = field(default_factory=dict)
    # The provider's own raw session slice.
    session: dict[str, Any] = field(default_factory=dict)
    llm: ResolvedLlm | None = None


@runtime_checkable
class Provider(Protocol):
    id: str
    label: str
    auth_mode: AuthMode
    session_cls: type[ProviderSessionState] | None

    async def discover_tools(self) -> list[Tool]:
        """Return the provider's tools (MCP: from list_tools; HTTP: declared)."""
        ...

    async def execute(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        """Run one tool call and return a normalized result."""
        ...

    def build_attachments(self, results: list[ToolResult]) -> list[Attachment]:
        """Lift renderable cards out of this provider's results."""
        ...

    def merge(self, slice_: dict[str, Any], results: list[ToolResult]) -> dict[str, Any]:
        """Fold a turn's results into the provider's next session slice."""
        ...
