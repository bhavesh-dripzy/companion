"""Swiggy Instamart serviceability — the one fact only ``update_cart`` knows.

Swiggy exposes no read-only serviceability signal (``search_products`` returns a
catalog even for non-deliverable addresses), and a failed (non-serviceable)
``update_cart`` poisons the account cart's address pointer. So we validate an
address the only reliable way — by attempting an ``update_cart`` against it —
while keeping the user's cart safe:

* Empty cart  → DUMMY PROBE: search a staple at the address, try to add it,
  read the verdict, then ``clear_cart`` (drops the dummy).
* Non-empty   → MOVE-WITH-ROLLBACK: try moving the real items to the new
  address; if serviceable the cart has moved (we report any dropped items);
  if not, re-pin the old address so the cart is left exactly as it was.

All probe calls run in ONE MCP session (mcp-remote is spawned once) — issuing
each via a fresh `run_remote_tool` was slow and tripped SSE/rate errors. This is
deterministic plumbing (never interprets user intent), so it composes with the
prompt-first product resolution in the planner.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_MCP_URL = os.getenv("SWIGGY_INSTAMART_MCP_URL", "https://mcp.swiggy.com/im")
_PROBE_QUERY = "milk"


def _text(envelope: dict[str, Any]) -> str:
    """Human text from a tool envelope (error prose lands in data.raw_text)."""
    data = envelope.get("data")
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("raw_text"), str):
            return data["raw_text"]
        blocks = data.get("blocks")
        if isinstance(blocks, list):
            return " ".join(str(b.get("raw_text") or "") for b in blocks if isinstance(b, dict))
    return ""


def _is_not_serviceable(envelope: dict[str, Any]) -> bool:
    """True only for the explicit serviceability error (not stock / qty errors)."""
    if not envelope.get("is_error"):
        return False
    return "not serviceable" in _text(envelope).lower()


def _walk_find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, "", []):
            return obj[key]
        for v in obj.values():
            found = _walk_find(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_find(v, key)
            if found is not None:
                return found
    return None


def _cart_lines(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """[{spinId, quantity, name}] from a get_cart/update_cart structured payload."""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    items = data.get("items") or data.get("cartItems") or []
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        sid = str((it or {}).get("spinId") or (it or {}).get("spin_id") or "").strip()
        if not sid:
            continue
        try:
            qty = int(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        out.append({"spinId": sid, "quantity": qty, "name": it.get("itemName") or it.get("name")})
    return out


def _cart_address(envelope: dict[str, Any]) -> str | None:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    addr = data.get("selectedAddress")
    if isinstance(addr, str) and addr.strip():
        return addr.strip()
    return str(_walk_find(data.get("selectedAddressDetails"), "id") or "").strip() or None


async def _probe(user_id: str, address_id: str) -> dict[str, Any]:
    from core.providers.mcp.connection import call_tool, mcp_session
    from core.providers.mcp.remote import repo_root, stdio_spec
    from core.providers.mcp.result_parse import tool_result_payload

    spec = stdio_spec(_MCP_URL, user_id=user_id, root=repo_root())
    async with mcp_session(spec) as session:

        async def run(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            return tool_result_payload(await call_tool(session, tool, args))

        cart = await run("get_cart", {})
        existing = _cart_lines(cart)
        old_addr = _cart_address(cart)

        if not existing:
            # DUMMY PROBE (empty cart): search a staple, try to add, then clear.
            search = await run("search_products", {"query": _PROBE_QUERY, "addressId": address_id})
            spin = _walk_find(search.get("data"), "spinId")
            if not spin:
                logger.warning(
                    "serviceability: no probe spin for %s; assuming serviceable", address_id
                )
                return {"serviceable": True, "switched": False, "dropped": [], "message": ""}
            probe = await run(
                "update_cart",
                {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": address_id},
            )
            serviceable = not _is_not_serviceable(probe)
            await run("clear_cart", {})  # drop the dummy regardless
            return {
                "serviceable": serviceable,
                "switched": False,
                "dropped": [],
                "message": "" if serviceable else "Instamart doesn't deliver to this address yet.",
            }

        # MOVE-WITH-ROLLBACK (non-empty cart): try moving the real items.
        items = [{"spinId": i["spinId"], "quantity": i["quantity"]} for i in existing]
        moved = await run("update_cart", {"items": items, "selectedAddressId": address_id})
        if _is_not_serviceable(moved):
            if old_addr:  # roll back so the cart is left exactly as it was
                await run("update_cart", {"items": items, "selectedAddressId": old_addr})
            return {
                "serviceable": False,
                "switched": False,
                "dropped": [],
                "message": "Instamart doesn't deliver to this address — kept your existing cart.",
            }
        survived = {i["spinId"] for i in _cart_lines(moved)}
        dropped = [i.get("name") or i["spinId"] for i in existing if i["spinId"] not in survived]
        return {"serviceable": True, "switched": True, "dropped": dropped, "message": ""}


def check_serviceability_and_switch(user_id: str, address_id: str) -> dict[str, Any]:
    """Validate ``address_id`` for Instamart, keeping the user's cart safe.

    Returns ``{serviceable, switched, dropped, message}``."""
    return asyncio.run(_probe(user_id, address_id))
