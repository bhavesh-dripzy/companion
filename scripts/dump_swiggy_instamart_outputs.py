"""Dump raw Swiggy Instamart MCP responses for representative cases.

Drives the LIVE Instamart MCP via mcp-remote (same OAuth tokens the executor
uses) and writes each call as a JSON file under ``swiggy_updated_outputs/`` with
BOTH the input we sent and the raw output exactly as the server returned it:

    {
      "tool": "...",
      "input": { ...arguments we sent... },
      "raw": {
        "is_error": bool,
        "structured_content": <server structuredContent, verbatim>,
        "content": [ {"type": "text", "text": "..."} ]   # raw content blocks
      }
    }

The cart-mutating cases (update_cart add / change-qty / remove, clear_cart) ARE
exercised so we can see their real shapes, then the cart is cleared at the end
to leave it clean. checkout / order placement is NOT called (real money).

Usage:
    ./.venv/bin/python scripts/dump_swiggy_instamart_outputs.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "swiggy_updated_outputs"
MCP_URL = os.environ.get("SWIGGY_INSTAMART_MCP_URL", "https://mcp.swiggy.com/im")
USER = os.environ.get("DUMP_USER", "local_admin")

# A known-serviceable saved address (Starrrs / Gurugram). Override via env.
ADDRESS_ID = os.environ.get("DUMP_ADDRESS_ID", "d8ji0f8ukngg9o52c900")

SEARCH_QUERIES = ("milk", "amul gold milk", "aashirvaad atta 5kg", "bread", "onions")

_counter = 0


def raw_payload(result: Any) -> dict[str, Any]:
    """The server's response captured verbatim (text blocks + structuredContent)."""
    content = []
    for b in getattr(result, "content", []) or []:
        content.append({"type": getattr(b, "type", None), "text": getattr(b, "text", None)})
    return {
        "is_error": bool(getattr(result, "isError", False)),
        "structured_content": getattr(result, "structuredContent", None),
        "content": content,
    }


def write(name: str, tool: str, arguments: dict[str, Any], raw: dict[str, Any]) -> None:
    global _counter
    _counter += 1
    path = OUT / f"{_counter:02d}_{name}.json"
    path.write_text(
        json.dumps({"tool": tool, "input": arguments, "raw": raw}, indent=2, default=str, ensure_ascii=False)
    )
    sc = raw.get("structured_content")
    shape = f"structured={type(sc).__name__}" + (f"(keys={sorted(sc)[:6]})" if isinstance(sc, dict) else "")
    err = " [ERROR]" if raw.get("is_error") else ""
    print(f"  wrote {path.relative_to(REPO)}  ({shape}){err}")


def walk_find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, "", []):
            return obj[key]
        for v in obj.values():
            found = walk_find(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = walk_find(v, key)
            if found is not None:
                return found
    return None


async def call(session, tool: str, arguments: dict[str, Any]) -> Any:
    print(f"-> {tool}  input={json.dumps(arguments, default=str)[:140]}")
    return await asyncio.wait_for(session.call_tool(tool, arguments=arguments), timeout=90)


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    proxy = REPO / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy.is_file():
        print("mcp-remote missing — run `npm install` at repo root.", file=sys.stderr)
        sys.exit(1)
    env = {**os.environ, "MCP_REMOTE_CONFIG_DIR": str(REPO / f".mcp-auth/users/{USER}")}
    params = StdioServerParameters(command="node", args=[str(proxy), MCP_URL], cwd=str(REPO), env=env)
    OUT.mkdir(parents=True, exist_ok=True)

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # 0. Tools catalog (names + input schemas)
            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            (OUT / "00_tools_catalog.json").write_text(
                json.dumps(
                    {"tools": [{"name": t.name, "description": t.description, "input_schema": t.inputSchema} for t in tools.tools]},
                    indent=2, default=str, ensure_ascii=False,
                )
            )
            print(f"  wrote swiggy_updated_outputs/00_tools_catalog.json ({len(tools.tools)} tools)")

            async def dump(name, tool, args):
                res = await call(session, tool, args)
                write(name, tool, args, raw_payload(res))
                return res

            # 1. Saved addresses
            await dump("get_addresses", "get_addresses", {})

            # 2. Searches (capture each query's raw shape)
            top_spin = None
            for q in SEARCH_QUERIES:
                res = await dump(f"search_products_{q.replace(' ', '_')}", "search_products", {"query": q, "addressId": ADDRESS_ID})
                if top_spin is None:
                    top_spin = walk_find(getattr(res, "structuredContent", None), "spinId") or walk_find(
                        raw_payload(res), "spinId"
                    )

            # 3. your_go_to_items
            try:
                await dump("your_go_to_items", "your_go_to_items", {"addressId": ADDRESS_ID})
            except Exception as e:  # noqa: BLE001
                print(f"  (your_go_to_items failed: {e})")

            # 4. Cart read (before)
            await dump("get_cart_before", "get_cart", {})

            # 5. update_cart — ADD one item
            if top_spin:
                await dump("update_cart_add", "update_cart", {"items": [{"spinId": top_spin, "quantity": 1}], "selectedAddressId": ADDRESS_ID})
                await dump("get_cart_after_add", "get_cart", {})
                # 6. update_cart — CHANGE qty to 2
                await dump("update_cart_setqty_2", "update_cart", {"items": [{"spinId": top_spin, "quantity": 2}], "selectedAddressId": ADDRESS_ID})
                # 7. update_cart — REMOVE (qty 0)
                await dump("update_cart_remove_qty0", "update_cart", {"items": [{"spinId": top_spin, "quantity": 0}], "selectedAddressId": ADDRESS_ID})
            else:
                print("  (no spinId harvested — skipping update_cart cases)")

            # 8. clear_cart + empty read
            try:
                await dump("clear_cart", "clear_cart", {})
            except Exception as e:  # noqa: BLE001
                print(f"  (clear_cart failed: {e})")
            await dump("get_cart_empty", "get_cart", {})

            # 9. Orders + tracking
            try:
                res = await dump("get_orders", "get_orders", {})
                oid = walk_find(getattr(res, "structuredContent", None), "orderId") or walk_find(
                    raw_payload(res), "orderId"
                )
                if oid:
                    await dump("track_order", "track_order", {"orderId": oid})
                else:
                    print("  (no orderId harvested — skipping track_order)")
            except Exception as e:  # noqa: BLE001
                print(f"  (get_orders/track failed: {e})")

    print(f"\nDONE. Files in {OUT.relative_to(REPO)}/ (cart left empty).")


if __name__ == "__main__":
    asyncio.run(main())
