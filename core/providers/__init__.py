"""Providers: the plugin interface, shared MCP/HTTP bases, and concrete plugins."""

from core.providers.base import ExecCtx, Provider

__all__ = ["ExecCtx", "Provider"]
