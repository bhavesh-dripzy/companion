"""The single registration point for providers and tools.

Today it seeds the tool registry from the builtin catalog. As plugins are ported
(Phase 4), each provider is registered here in one line and its `discover_tools()`
populates the registry — no other file changes when a provider is added (ADR-0002).
"""

from __future__ import annotations

import os
from functools import lru_cache

from core.registry.provider_registry import ProviderRegistry
from core.registry.seed import builtin_tools
from core.registry.tool_registry import ToolRegistry

# Providers cut over to their clean `McpProvider` by default (full card parity +
# session parity, live-validated). The rest still run via the proven
# LegacyMcpAdapter until their mutating-flow cards land (swiggy/instamart/zepto
# cart, district reservation, kite order-ticket). Override with CLEAN_MCP (an
# explicit comma list, empty = none); LEGACY_PIPELINE=1 reverts the whole pipeline.
_DEFAULT_CLEAN_MCP = frozenset(
    {"zomato", "swiggy_food", "swiggy_instamart", "zepto", "district", "kite"}
)


def _clean_mcp_ids() -> frozenset[str]:
    raw = os.getenv("CLEAN_MCP")
    if raw is None:
        return _DEFAULT_CLEAN_MCP
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


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

    # MCP providers: clean McpProvider for the cut-over ids, else the migration
    # LegacyMcpAdapter (proven executor). Tools come from the seed catalog either way.
    from core.providers.legacy import MCP_CONFIGS, LegacyMcpAdapter

    clean_ids = _clean_mcp_ids()
    clean_classes = _clean_provider_classes()
    for cfg in MCP_CONFIGS:
        clean_cls = clean_classes.get(cfg.id)
        if clean_cls is not None and cfg.id in clean_ids:
            _register(registry, clean_cls())
        else:
            _register(registry, LegacyMcpAdapter(cfg))
    return registry


def _clean_provider_classes() -> dict[str, type]:
    """The clean McpProvider class per MCP provider id (lazy import)."""
    from core.providers.plugins.district import DistrictProvider
    from core.providers.plugins.kite import KiteProvider
    from core.providers.plugins.swiggy_food import SwiggyFoodProvider
    from core.providers.plugins.swiggy_instamart import SwiggyInstamartProvider
    from core.providers.plugins.zepto import ZeptoProvider
    from core.providers.plugins.zomato import ZomatoProvider

    return {
        "zomato": ZomatoProvider,
        "swiggy_food": SwiggyFoodProvider,
        "swiggy_instamart": SwiggyInstamartProvider,
        "zepto": ZeptoProvider,
        "district": DistrictProvider,
        "kite": KiteProvider,
    }


@lru_cache(maxsize=1)
def default_registry() -> ProviderRegistry:
    """Process-wide registry singleton."""
    return build_registry()
