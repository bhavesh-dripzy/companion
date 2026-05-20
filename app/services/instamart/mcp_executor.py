"""
Run Swiggy Instamart MCP tool_plan via mcp-remote (same wire stack as Swiggy Food).

Execution is delegated to ``app.services.swiggy.mcp_executor`` with the Instamart
endpoint URL defaulting to ``https://mcp.swiggy.com/im``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.services.swiggy.mcp_executor import (
    list_swiggy_mcp_tools_sync,
    run_swiggy_tool_plan_sync,
)

_DEFAULT_INSTAMART_MCP_URL = "https://mcp.swiggy.com/im"


def _instamart_url(mcp_url: str | None) -> str:
    return (
        (mcp_url or "").strip()
        or os.getenv("SWIGGY_INSTAMART_MCP_URL", "").strip()
        or _DEFAULT_INSTAMART_MCP_URL
    )


def run_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    location_text: str | None = None,
) -> dict[str, Any]:
    return run_swiggy_tool_plan_sync(
        tool_plan,
        address_id=address_id,
        repo_root=repo_root,
        mcp_url=_instamart_url(mcp_url),
        node_binary=node_binary,
        user_id=user_id,
        location_text=location_text,
    )


def list_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return list_swiggy_mcp_tools_sync(
        repo_root=repo_root,
        mcp_url=_instamart_url(mcp_url),
        node_binary=node_binary,
        user_id=user_id,
    )
