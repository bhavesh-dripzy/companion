"""Migration adapters: run MCP providers via their proven legacy executors."""

from core.providers.legacy.adapter import LegacyMcpAdapter, LegacyMcpConfig
from core.providers.legacy.configs import MCP_CONFIGS

__all__ = ["MCP_CONFIGS", "LegacyMcpAdapter", "LegacyMcpConfig"]
