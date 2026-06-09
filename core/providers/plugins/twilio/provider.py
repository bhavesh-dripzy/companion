"""TwilioProvider — outbound voice calls as a stateless HTTP provider.

The whole point of #27: a non-MCP provider with no addresses/cart/menu state.
It declares two tools with real JSON-Schema params and handles them directly —
no MCP server, no schema surgery, no edits outside this folder + one bootstrap line.
"""

from __future__ import annotations

import asyncio

from core.contracts.tool import AuthMode, ConfirmPolicy, Tool
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode, ToolResult
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.http.http_base import HttpProvider
from core.providers.plugins.twilio import client

_TOOLS = [
    Tool(
        name="make_phone_call",
        provider="twilio",
        description="Place an outbound voice call that speaks a message via Gemini Live.",
        params={
            "type": "object",
            "properties": {
                "to_number": {"type": "string", "description": "Destination phone (E.164)"},
                "message": {"type": "string", "description": "What to say on the call"},
            },
            "required": ["to_number", "message"],
        },
        auth_mode=AuthMode.NONE,
        confirm=ConfirmPolicy.SINGLE,
        mutating=True,
        source="static",
    ),
    Tool(
        name="get_call_status",
        provider="twilio",
        description="Fetch the current status of a placed call.",
        params={
            "type": "object",
            "properties": {"call_sid": {"type": "string"}},
            "required": ["call_sid"],
        },
        auth_mode=AuthMode.NONE,
        source="static",
    ),
]


class TwilioProvider(HttpProvider):
    id = "twilio"
    label = "Twilio Voice Calls"
    auth_mode = AuthMode.NONE
    session_cls = None

    def declared_tools(self) -> list[Tool]:
        return list(_TOOLS)

    async def execute_one(self, call: ToolCall, ctx: ExecCtx) -> ToolResult:
        args = call.arguments
        if call.tool == "make_phone_call":
            to_number = str(args.get("to_number") or args.get("to") or "").strip()
            message = str(args.get("message") or "").strip()
            if not to_number or not message:
                raise InvalidArgsError("make_phone_call needs to_number and message")
            result = await asyncio.to_thread(
                client.make_call, to_number=to_number, message=message, user_id=ctx.user_id
            )
        elif call.tool == "get_call_status":
            sid = str(args.get("call_sid") or args.get("execution_sid") or "").strip()
            if not sid:
                raise InvalidArgsError("get_call_status needs call_sid")
            result = await asyncio.to_thread(client.get_call_status, sid, ctx.user_id)
        else:
            raise InvalidArgsError(f"unknown twilio tool: {call.tool}")

        ok = bool(result.get("ok"))
        return ToolResult(
            tool=call.tool,
            provider="twilio",
            ok=ok,
            arguments=dict(args),
            data=result,
            error=None if ok else ErrorCode.UPSTREAM_ERROR,
            error_detail=None if ok else str(result.get("error") or "call failed"),
        )
