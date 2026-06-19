"""Zepto serviceability — validate a saved address before pinning it as default.

Far simpler (and safer) than Instamart's: Zepto's ``select_saved_address``
binds the store from the saved address directly, so selecting it IS the
serviceability probe — a serviceable address returns "Address selected …" with a
store id; a non-deliverable one says so. It never touches the cart (Zepto's cart
is server-side per-user and persists across stores), so there's nothing to roll
back.

The single probe call runs in ONE MCP session. Deterministic plumbing — never
interprets user intent — so it composes with the prompt-first planner.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_MCP_URL = os.getenv("ZEPTO_MCP_URL", "https://mcp.zepto.co.in/mcp")
_NOT_SERVICEABLE_MARKERS = ("does not currently deliver", "not serviceable", "not deliverable")


def _text(envelope: dict[str, Any]) -> str:
    """Prose across the envelope shapes Zepto uses: a bare string,
    ``{summary|raw_text|text: "…"}``, or a nested ``{data: …}``."""
    data: Any = envelope
    for _ in range(3):
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            for key in ("summary", "raw_text", "text"):
                if isinstance(data.get(key), str) and data[key].strip():
                    return data[key]
            if "data" in data:
                data = data["data"]
                continue
        break
    return ""


def _is_serviceable(envelope: dict[str, Any]) -> bool:
    """True when select_saved_address bound a store (the serviceability signal)."""
    text = _text(envelope).lower()
    if envelope.get("is_error"):
        return False
    if any(m in text for m in _NOT_SERVICEABLE_MARKERS):
        return False
    return "address selected" in text or "store id" in text


async def _probe(user_id: str, address_id: str) -> dict[str, Any]:
    from core.providers.mcp.connection import call_tool, mcp_session
    from core.providers.mcp.remote import repo_root, stdio_spec
    from core.providers.mcp.result_parse import tool_result_payload

    spec = stdio_spec(_MCP_URL, user_id=user_id, root=repo_root())
    async with mcp_session(spec) as session:
        res = tool_result_payload(
            await call_tool(session, "select_saved_address", {"addressId": address_id})
        )
        serviceable = _is_serviceable(res)
        return {
            "serviceable": serviceable,
            "switched": False,
            "dropped": [],
            "message": "" if serviceable else "Zepto doesn't deliver to this address yet.",
        }


def check_serviceability_and_switch(user_id: str, address_id: str) -> dict[str, Any]:
    """Validate ``address_id`` for Zepto. Returns ``{serviceable, switched, dropped, message}``."""
    return asyncio.run(_probe(user_id, address_id))
