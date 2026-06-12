"""Kite raw streamable-HTTP transport — persistent session re-attach.

Kite's MCP auth is bound to the server-issued ``Mcp-Session-Id``: the login URL
embeds it, and a NEW connection presenting the header re-attaches to the same
(authed) session — but the server mints a fresh session on every ``initialize``,
so mcp-remote (which always initializes) can never ride an authed session.
This module talks to the server directly: initialize once at Connect time,
vault the session id per user, then re-attach with bare ``tools/call`` requests
(no initialize) for status probes and chat turns.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import requests

from core.providers.mcp.token_store import mcp_token_secret, merge_mcp_token_secret

_DEFAULT_URL = "https://mcp.kite.trade/mcp"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_TIMEOUT = 20


def _url() -> str:
    return os.getenv("KITE_MCP_URL", _DEFAULT_URL)


def parse_rpc_text(text: str, content_type: str) -> dict[str, Any] | None:
    """JSON-RPC payload from a streamable-HTTP response (SSE or plain JSON)."""
    if "text/event-stream" in (content_type or ""):
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def result_shim(rpc: dict[str, Any] | None) -> Any:
    """Wrap a JSON-RPC tools/call payload as an SDK-shaped CallToolResult so
    `tool_result_payload` parses it unchanged."""
    if not isinstance(rpc, dict):
        return SimpleNamespace(content=[], isError=True)
    if rpc.get("error"):
        message = str((rpc["error"] or {}).get("message") or "MCP error")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=message)], isError=True)
    result = rpc.get("result") or {}
    blocks = [
        SimpleNamespace(
            type=str(blk.get("type") or "text"),
            text=blk.get("text") or "",
            data=blk.get("data"),
            mimeType=blk.get("mimeType"),
            resource=None,
        )
        for blk in (result.get("content") or [])
        if isinstance(blk, dict)
    ]
    return SimpleNamespace(content=blocks, isError=bool(result.get("isError")))


def _post_sync(body: dict[str, Any], sid: str | None = None) -> requests.Response:
    headers = dict(_HEADERS)
    if sid:
        headers["Mcp-Session-Id"] = sid
    return requests.post(_url(), json=body, headers=headers, timeout=_TIMEOUT)


def start_login() -> dict[str, Any]:
    """Mint a fresh Kite MCP session and its Zerodha login URL."""
    r = _post_sync(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "Companion", "version": "1.0"},
            },
        }
    )
    sid = r.headers.get("Mcp-Session-Id") or r.headers.get("mcp-session-id")
    if not r.ok or not sid:
        raise RuntimeError(f"Kite MCP initialize failed (HTTP {r.status_code}).")
    _post_sync({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid)
    r2 = _post_sync(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "login", "arguments": {}},
        },
        sid=sid,
    )
    rpc = parse_rpc_text(r2.text, r2.headers.get("Content-Type", ""))
    text = ""
    for blk in ((rpc or {}).get("result") or {}).get("content") or []:
        if isinstance(blk, dict) and blk.get("type") == "text":
            text += str(blk.get("text") or "")
    match = re.search(r"https://kite\.zerodha\.com/\S+", text)
    if not match:
        raise RuntimeError("Kite login tool did not return a login URL.")
    return {"sid": sid, "authorization_url": match.group(0).rstrip(").,"), "notice": text[:400]}


def probe_profile(sid: str) -> dict[str, Any]:
    """Is this vaulted session authenticated? (get_profile on the session.)"""
    try:
        r = _post_sync(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_profile", "arguments": {}},
            },
            sid=sid,
        )
    except requests.RequestException as e:
        return {"authed": False, "session_alive": False, "detail": str(e)}
    if not r.ok:
        return {"authed": False, "session_alive": False, "detail": f"HTTP {r.status_code}"}
    rpc = parse_rpc_text(r.text, r.headers.get("Content-Type", "")) or {}
    if rpc.get("error"):
        return {
            "authed": False,
            "session_alive": False,
            "detail": str((rpc["error"] or {}).get("message") or "session error"),
        }
    result = rpc.get("result") or {}
    text = "".join(
        str(blk.get("text") or "") for blk in (result.get("content") or []) if isinstance(blk, dict)
    )
    if result.get("isError"):
        return {"authed": False, "session_alive": True, "detail": text[:200]}
    name = None
    try:
        payload = json.loads(text)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            name = data.get("user_name") or data.get("user_id")
    except json.JSONDecodeError:
        pass
    return {"authed": True, "session_alive": True, "profile": name}


# ── vaulted session id (Secrets Manager, per user) ────────────────────────


def vaulted_sid(user_id: str | None) -> str | None:
    if not user_id:
        return None
    payload = mcp_token_secret(user_id)
    session = payload.get("kite_session") if isinstance(payload, dict) else None
    sid = session.get("sid") if isinstance(session, dict) else None
    return str(sid) if sid else None


def save_sid(user_id: str, sid: str) -> None:
    merge_mcp_token_secret(user_id, kite_session={"sid": sid})


def clear_sid(user_id: str) -> None:
    merge_mcp_token_secret(user_id, kite_session={})


# ── async re-attach session for chat turns ────────────────────────────────


class RawKiteSession:
    """Duck-typed stand-in for the SDK ClientSession over a re-attached
    (already-initialized, possibly authed) Kite MCP session."""

    def __init__(self, client: Any, sid: str) -> None:
        self._client = client
        self._sid = sid
        self._next_id = 10

    async def _post(self, body: dict[str, Any]) -> Any:
        return await self._client.post(
            _url(), json=body, headers={**_HEADERS, "Mcp-Session-Id": self._sid}
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        r = await self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        return result_shim(parse_rpc_text(r.text, r.headers.get("Content-Type", "")))

    async def list_tools(self) -> Any:
        self._next_id += 1
        r = await self._post({"jsonrpc": "2.0", "id": self._next_id, "method": "tools/list"})
        rpc = parse_rpc_text(r.text, r.headers.get("Content-Type", "")) or {}
        tools = [
            SimpleNamespace(
                name=t.get("name"),
                description=t.get("description", ""),
                inputSchema=t.get("inputSchema"),
            )
            for t in ((rpc.get("result") or {}).get("tools") or [])
            if isinstance(t, dict)
        ]
        return SimpleNamespace(tools=tools)


@asynccontextmanager
async def raw_session(sid: str):
    """Async context yielding a RawKiteSession re-attached to ``sid``."""
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        yield RawKiteSession(client, sid)
