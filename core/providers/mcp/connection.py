"""MCP stdio session management (async).

Generic, provider-agnostic transport: spawn the configured stdio command (for
hosted servers, the `mcp-remote` proxy) and expose `list_tools` / `call_tool`.
Ported from `stdio_mcp_executor._run_stdio_session`. Token hydration / env and
the mcp-remote command are supplied by the concrete provider (Phase 4).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

INIT_TIMEOUT_SECONDS = 20
TOOL_TIMEOUT_SECONDS = 30


@dataclass
class StdioSpec:
    """How to launch an MCP server over stdio."""

    command: str
    args: list[str]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)


@asynccontextmanager
async def mcp_session(spec: StdioSpec, *, init_timeout: int = INIT_TIMEOUT_SECONDS):
    """Open an initialized MCP `ClientSession` over stdio; close on exit."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=spec.command, args=spec.args, cwd=spec.cwd, env=spec.env)
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=init_timeout)
        yield session


async def list_tools(session: Any, *, timeout: int = TOOL_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """Return discovered tools as `{name, description, input_schema}` dicts."""
    listing = await asyncio.wait_for(session.list_tools(), timeout=timeout)
    return [
        {
            "name": tool.name,
            "description": getattr(tool, "description", "") or "",
            "input_schema": getattr(tool, "inputSchema", None),
        }
        for tool in listing.tools
    ]


async def call_tool(
    session: Any, name: str, arguments: dict[str, Any], *, timeout: int = TOOL_TIMEOUT_SECONDS
) -> Any:
    """Invoke a tool and return the raw MCP result (parse with `tool_result_payload`)."""
    return await asyncio.wait_for(session.call_tool(name, arguments=arguments), timeout=timeout)
