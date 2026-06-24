"""Unit tests for the core data contracts retained for the MCP layer.

The tool-calling contracts (Tool, ToolCall, ToolPlan, SessionState, Attachment,
…) were removed with the legacy orchestrator. Only the result taxonomy remains.
"""

import pytest

from core.contracts import ErrorCode, ToolResult

pytestmark = pytest.mark.unit


def test_tool_result_failed_flag():
    ok = ToolResult(tool="t", provider="p", ok=True)
    bad = ToolResult(tool="t", provider="p", ok=False, error=ErrorCode.AUTH_REQUIRED)
    assert not ok.failed
    assert bad.failed and bad.error is ErrorCode.AUTH_REQUIRED
