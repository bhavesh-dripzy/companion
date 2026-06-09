"""WhatsAppProvider — send an outbound WhatsApp message (HTTP provider).

Strangler adapter over the existing `WhatsAppClient`. The inbound webhook stays a
web-layer route; only the outbound send is a tool. Confirm-gated (outward action).
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.contracts.tool import AuthMode, ConfirmPolicy, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ToolResult
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider

_TOOL = Tool(
    name="send_whatsapp_message",
    provider="whatsapp",
    description="Send a text message to a WhatsApp number.",
    params={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone (E.164, no +)"},
            "message": {"type": "string"},
        },
        "required": ["to", "message"],
    },
    auth_mode=AuthMode.NONE,
    confirm=ConfirmPolicy.SINGLE,
    mutating=True,
    source="static",
)


class WhatsAppProvider(HttpProvider):
    id = "whatsapp"
    label = "WhatsApp"
    auth_mode = AuthMode.NONE
    session_cls = None

    def declared_tools(self) -> list[Tool]:
        return [_TOOL]

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        if call.tool != "send_whatsapp_message":
            raise InvalidArgsError(f"unknown whatsapp tool: {call.tool}")
        to = str(call.arguments.get("to") or "").strip()
        message = str(call.arguments.get("message") or "").strip()
        if not to or not message:
            raise InvalidArgsError("send_whatsapp_message needs to and message")
        result = await asyncio.to_thread(self._send, to, message)
        ok = "error" not in result
        return ToolResult(
            tool=call.tool,
            provider="whatsapp",
            ok=ok,
            arguments=dict(call.arguments),
            data=result,
            error=None if ok else ErrorCode.UPSTREAM_ERROR,
            error_detail=None if ok else str(result.get("error") or ""),
        )

    @staticmethod
    def _send(to: str, message: str) -> dict[str, Any]:
        from companion_api.whatsapp_client import WhatsAppClient

        return WhatsAppClient().send_text_message(to, message)
