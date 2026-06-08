"""Shared MCP provider base and helpers."""

from core.providers.mcp.address import extract_address_rows, first_address_id
from core.providers.mcp.connection import StdioSpec, call_tool, list_tools, mcp_session
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.placeholders import PlaceholderResolver
from core.providers.mcp.result_parse import tool_result_payload

__all__ = [
    "McpProvider",
    "PlaceholderResolver",
    "StdioSpec",
    "call_tool",
    "extract_address_rows",
    "first_address_id",
    "list_tools",
    "mcp_session",
    "tool_result_payload",
]
