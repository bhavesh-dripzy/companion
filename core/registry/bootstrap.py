"""The single registration point for providers and tools.

Today it seeds the tool registry from the builtin catalog. As plugins are ported
(Phase 4), each provider is registered here in one line and its `discover_tools()`
populates the registry — no other file changes when a provider is added (ADR-0002).
"""

from __future__ import annotations

from functools import lru_cache

from core.registry.provider_registry import ProviderRegistry
from core.registry.seed import builtin_tools
from core.registry.tool_registry import ToolRegistry


def build_registry() -> ProviderRegistry:
    """Construct a fresh registry with the builtin tool catalog loaded."""
    tools = ToolRegistry()
    tools.register_all(builtin_tools())
    registry = ProviderRegistry(tools=tools)
    # Plugin registration lands here in Phase 4, e.g.:
    #   from core.providers.plugins.zomato import ZomatoProvider
    #   registry.register(ZomatoProvider())
    return registry


@lru_cache(maxsize=1)
def default_registry() -> ProviderRegistry:
    """Process-wide registry singleton."""
    return build_registry()
