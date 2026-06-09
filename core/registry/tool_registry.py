"""The tool registry: the single index of every tool in the system.

Tools land here from MCP discovery or static HTTP declarations. From this index
the middleware derives the planner prompt, argument validation, and tool→provider
dispatch — there is no separate `_ALLOWED_TOOLS` set to keep in sync (ADR-0002).
"""

from __future__ import annotations

from core.contracts.tool import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._by_provider: dict[str, list[Tool]] = {}
        self._index: dict[str, Tool] = {}  # canonical name + aliases -> Tool

    def register(self, tool: Tool) -> None:
        """Add or replace a tool (idempotent on (provider, name))."""
        bucket = self._by_provider.setdefault(tool.provider, [])
        for existing in [t for t in bucket if t.name == tool.name]:
            bucket.remove(existing)
        bucket.append(tool)
        self._index[tool.name] = tool
        for alias in tool.aliases:
            self._index[alias] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str, provider: str | None = None) -> Tool | None:
        """Resolve a tool by canonical name or alias, optionally scoped to a provider.

        When scoped, the provider's own bucket is searched so tools whose names
        collide across providers (e.g. `get_order_history` in zomato and kite)
        resolve correctly. Unscoped lookups use the global index (last registered
        wins on a name collision; the planner always supplies a provider).
        """
        if provider is not None:
            for tool in self._by_provider.get(provider, []):
                if tool.matches(name):
                    return tool
            return None
        return self._index.get(name)

    def for_provider(self, provider: str) -> list[Tool]:
        return list(self._by_provider.get(provider, []))

    def provider_of(self, name: str) -> str | None:
        tool = self._index.get(name)
        return tool.provider if tool else None

    def all(self) -> list[Tool]:
        return [tool for bucket in self._by_provider.values() for tool in bucket]

    def providers(self) -> list[str]:
        return list(self._by_provider.keys())

    def __len__(self) -> int:
        return len(self.all())
