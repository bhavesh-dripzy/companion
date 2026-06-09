"""The provider registry: stores plugins and routes tool calls to them.

Routing replaces the legacy `if name == "zomato" / elif ...` dispatch with a
data-driven lookup (provider hint, else the tool's owning provider). Unknown
tools fall into the `_unsupported` bucket, mirroring the legacy behaviour.
"""

from __future__ import annotations

from core.contracts.tool_call import ToolCall
from core.providers.base import Provider
from core.registry.tool_registry import ToolRegistry

UNSUPPORTED = "_unsupported"


class ProviderRegistry:
    def __init__(self, tools: ToolRegistry | None = None) -> None:
        self.tools = tools or ToolRegistry()
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> Provider | None:
        return self._providers.get(provider_id)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def ids(self) -> list[str]:
        return list(self._providers.keys())

    def provider_for_tool(self, name: str, hint: str | None = None) -> str:
        """Resolve which provider owns a tool: a valid hint wins, else the index."""
        if hint and hint in self._providers:
            return hint
        return self.tools.provider_of(name) or UNSUPPORTED

    def route(self, calls: list[ToolCall]) -> dict[str, list[ToolCall]]:
        """Group calls by owning provider, preserving order within each group."""
        grouped: dict[str, list[ToolCall]] = {}
        for call in calls:
            pid = self.provider_for_tool(call.tool, call.provider)
            grouped.setdefault(pid, []).append(call)
        return grouped
