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


def _register(registry: ProviderRegistry, provider: object) -> None:
    """Register a plugin and, for HTTP plugins, its declared tools (real schemas
    replace the seed entries). MCP plugins discover their tools at runtime."""
    registry.register(provider)  # type: ignore[arg-type]
    declared = getattr(provider, "declared_tools", None)
    if callable(declared):
        registry.tools.register_all(declared())


def build_registry() -> ProviderRegistry:
    """Construct a fresh registry: the builtin catalog plus every plugin.

    Adding a provider is one line here — see CONTRIBUTING.md ("Adding a provider").
    """
    tools = ToolRegistry()
    tools.register_all(builtin_tools())
    registry = ProviderRegistry(tools=tools)

    from core.providers.plugins.calendar import CalendarProvider
    from core.providers.plugins.maps import MapsProvider
    from core.providers.plugins.twilio import TwilioProvider
    from core.providers.plugins.whatsapp import WhatsAppProvider

    for plugin in (
        TwilioProvider(),
        MapsProvider(),
        CalendarProvider(),
        WhatsAppProvider(),
    ):
        _register(registry, plugin)

    # MCP providers run via their proven legacy executors during migration; their
    # tools come from the seed catalog until live-fixture-validated clean ports.
    from core.providers.legacy import MCP_CONFIGS, LegacyMcpAdapter

    for cfg in MCP_CONFIGS:
        _register(registry, LegacyMcpAdapter(cfg))
    return registry


@lru_cache(maxsize=1)
def default_registry() -> ProviderRegistry:
    """Process-wide registry singleton."""
    return build_registry()
