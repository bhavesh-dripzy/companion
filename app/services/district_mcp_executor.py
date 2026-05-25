"""Backward-compat shim — import from ``app.services.district.mcp_executor``."""

from app.services.district.mcp_executor import (  # noqa: F401
    list_district_mcp_tools_sync,
    run_district_tool_plan_sync,
)
