"""UberProvider — cab estimate/book/status/cancel (HTTP provider).

A strangler adapter over the existing `execute_uber_cab` (which wraps the Uber
API client). Each tool maps to a legacy `action`; book/cancel are confirm-gated.
The client internals move into this plugin at cutover (Phase 8).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool import AuthMode, ConfirmPolicy, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ToolResult
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider

_ACTION = {
    "estimate_cab": "estimate",
    "book_cab": "book",
    "cab_status": "status",
    "cancel_cab": "cancel",
}


def _tool(
    name: str, desc: str, props: dict[str, Any], required: list[str], confirm: ConfirmPolicy
) -> Tool:
    return Tool(
        name=name,
        provider="uber",
        description=desc,
        params={"type": "object", "properties": props, "required": required},
        auth_mode=AuthMode.NONE,
        confirm=confirm,
        mutating=confirm is not ConfirmPolicy.NEVER,
        source="static",
    )


_DEST = {
    "destination_text": {"type": "string"},
    "product_hint": {"type": "string"},
    "seat_count": {"type": "integer"},
}
_TOOLS = [
    _tool(
        "estimate_cab",
        "Estimate fare/ETA for a cab to a destination.",
        _DEST,
        ["destination_text"],
        ConfirmPolicy.NEVER,
    ),
    _tool(
        "book_cab",
        "Book a cab to a destination.",
        _DEST,
        ["destination_text"],
        ConfirmPolicy.SINGLE,
    ),
    _tool(
        "cab_status",
        "Check a booked ride's status.",
        {"request_id": {"type": "string"}},
        ["request_id"],
        ConfirmPolicy.NEVER,
    ),
    _tool(
        "cancel_cab",
        "Cancel a booked ride.",
        {"request_id": {"type": "string"}},
        ["request_id"],
        ConfirmPolicy.SINGLE,
    ),
]


class UberProvider(HttpProvider):
    id = "uber"
    label = "Uber"
    auth_mode = AuthMode.NONE
    session_cls = None

    def declared_tools(self) -> list[Tool]:
        return list(_TOOLS)

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        action = _ACTION.get(call.tool)
        if action is None:
            raise InvalidArgsError(f"unknown uber tool: {call.tool}")
        plan = {"action": action, **call.arguments}
        result = await asyncio.to_thread(self._run, plan, ctx.user_id, ctx.user_geo)
        ok = bool(result.get("ok"))
        return ToolResult(
            tool=call.tool,
            provider="uber",
            ok=ok,
            arguments=dict(call.arguments),
            data=result,
            error=None if ok else ErrorCode.UPSTREAM_ERROR,
            error_detail=None if ok else str(result.get("error") or ""),
        )

    @staticmethod
    def _run(plan: dict[str, Any], user_id: str | None, user_geo: Any) -> dict[str, Any]:
        from app.services.uber.executor import execute_uber_cab

        return execute_uber_cab(plan=plan, user_id=user_id or "", user_geo=user_geo)

    def build_attachments(self, results: list[ToolResult]) -> list[Attachment]:
        cards = []
        for result in results:
            data = result.data or {}
            if data.get("deep_link") or data.get("estimate"):
                cards.append(
                    Attachment(
                        kind="cab_card",
                        deep_link=data.get("deep_link"),
                        estimate=data.get("estimate"),
                        product=data.get("product"),
                    )
                )
        return cards
