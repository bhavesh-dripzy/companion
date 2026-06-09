"""Registry: the tool index, the provider store, and the single bootstrap point."""

from core.registry.bootstrap import build_registry, default_registry
from core.registry.provider_registry import UNSUPPORTED, ProviderRegistry
from core.registry.tool_registry import ToolRegistry

__all__ = [
    "UNSUPPORTED",
    "ProviderRegistry",
    "ToolRegistry",
    "build_registry",
    "default_registry",
]
