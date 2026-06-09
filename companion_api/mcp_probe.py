"""Sync MCP probe helpers for the Settings connection-test / tool-discovery routes.

A thin sync bridge over core's async MCP transport (`core.providers.mcp.connection`
+ `remote`) — the same primitives the pipeline uses — to list tools or run a single
tool against a hosted (mcp-remote) or stdio MCP server. Deliberately stateless: the
multi-turn cart/menu/address carry-forward lives in the pipeline, not here.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from core.providers.mcp.connection import StdioSpec, call_tool, list_tools, mcp_session
from core.providers.mcp.remote import repo_root, stdio_env, stdio_spec
from core.providers.mcp.result_parse import tool_result_payload

logger = logging.getLogger(__name__)


def _humanize(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


async def _alist_remote(mcp_url: str, user_id: str | None, root: Path) -> list[dict[str, Any]]:
    async with mcp_session(stdio_spec(mcp_url, user_id=user_id, root=root)) as session:
        return await list_tools(session)


async def _alist_stdio(
    command: str, args: list[str], user_id: str | None, root: Path
) -> list[dict[str, Any]]:
    spec = StdioSpec(command=command, args=list(args), cwd=str(root), env=stdio_env(user_id, root))
    async with mcp_session(spec) as session:
        return await list_tools(session)


async def _arun_remote(
    mcp_url: str, tool: str, arguments: dict[str, Any], user_id: str | None, root: Path
) -> dict[str, Any]:
    async with mcp_session(stdio_spec(mcp_url, user_id=user_id, root=root)) as session:
        raw = await call_tool(session, tool, arguments)
    return tool_result_payload(raw)


def list_remote_tools(mcp_url: str, *, user_id: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """List tools exposed by a hosted (mcp-remote) MCP server. Tokens hydrated first."""
    root = root or repo_root()
    try:
        return {"ok": True, "tools": asyncio.run(_alist_remote(mcp_url, user_id, root)), "mcp_url": mcp_url}
    except Exception as exc:
        logger.warning("list_remote_tools failed for %s", mcp_url, exc_info=True)
        return {"ok": False, "tools": [], "error_detail": _humanize(exc), "mcp_url": mcp_url}


def list_stdio_tools(
    command: str, args: list[str], *, user_id: str | None = None, root: Path | None = None
) -> dict[str, Any]:
    """List tools exposed by a local stdio MCP server (e.g. the calorie tracker)."""
    root = root or repo_root()
    try:
        return {"ok": True, "tools": asyncio.run(_alist_stdio(command, args, user_id, root))}
    except Exception as exc:
        logger.warning("list_stdio_tools failed for %s", command, exc_info=True)
        return {"ok": False, "tools": [], "error_detail": _humanize(exc)}


def run_remote_tool(
    mcp_url: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Run a single tool against a hosted MCP server; returns {ok, data: envelope}."""
    root = root or repo_root()
    try:
        envelope = asyncio.run(_arun_remote(mcp_url, tool, arguments or {}, user_id, root))
        return {"ok": not bool(envelope.get("is_error")), "data": envelope}
    except Exception as exc:
        logger.warning("run_remote_tool failed for %s/%s", mcp_url, tool, exc_info=True)
        return {"ok": False, "data": {}, "error_detail": _humanize(exc)}
