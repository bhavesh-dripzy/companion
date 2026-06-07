"""Backward-compat shim — import from ``app.services.zepto.mcp_executor``."""

from app.services.zepto.mcp_executor import (  # noqa: F401
    list_zepto_mcp_tools_sync,
    run_zepto_tool_plan_sync,
)
