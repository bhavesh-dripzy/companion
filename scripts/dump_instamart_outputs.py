"""Dump raw Swiggy Instamart MCP responses for representative tool calls.

Mirrors the Zepto / Zomato / Swiggy / District dump scripts. Drives the live
Instamart MCP (``https://mcp.swiggy.com/im``) via ``mcp-remote`` (same OAuth
tokens the Swiggy-food executor uses) and writes each call as a
``{tool, arguments, step_index, result}`` envelope into ``outputs/instamart/``.

**Safety**: every mutating tool is hard-skipped. We never:
- Modify the cart (``update_cart``, ``clear_cart``)
- Place an order (``checkout``)
- File a fake bug report (``report_error``)

Read-only tools we capture:
- ``get_addresses``
- ``get_cart``
- ``get_orders`` (active + all)
- ``track_order`` (only if an active order id is harvested)
- ``search_products`` for a spread of grocery keywords
- ``your_go_to_items`` (uses the harvested address id)

Usage:
    ./.venv/bin/python scripts/dump_instamart_outputs.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.services.zomato.mcp_executor import (  # noqa: E402
    _stdio_env,
    ensure_mcp_tokens_on_disk,
    repo_root_from_settings,
)

OUTPUT_DIR = REPO_ROOT / "outputs" / "instamart"
MCP_URL = "https://mcp.swiggy.com/im"
USER_ID = "local_admin"

# Spread of grocery queries — variety in response shape (dairy / bakery /
# protein / staples / produce / household).
SEARCH_KEYWORDS: tuple[str, ...] = (
    "milk",
    "bread",
    "eggs",
    "paneer",
    "atta",
    "bananas",
    "detergent",
)


def _payload(call_result: Any) -> dict[str, Any]:
    """Reduce an MCP ``CallToolResult`` into a JSON-serializable envelope."""
    is_error = bool(getattr(call_result, "isError", False))
    structured = getattr(call_result, "structuredContent", None)
    text_chunks: list[str] = []
    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_chunks.append(text)

    data: Any = None
    if text_chunks:
        joined = "\n".join(text_chunks).strip()
        try:
            data = json.loads(joined)
        except Exception:
            data = joined
    elif structured is not None:
        data = structured

    return {"is_error": is_error, "data": data, "structured": structured}


def _write(name: str, envelope: dict[str, Any]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"{name}.json"
    out.write_text(json.dumps(envelope, indent=2, ensure_ascii=False))
    body = envelope.get("result", {}).get("data")
    if isinstance(body, dict):
        summary = f"keys={sorted(body.keys())[:6]}"
    elif isinstance(body, list):
        summary = f"list len={len(body)}"
    elif isinstance(body, str):
        summary = f"text len={len(body)}"
    else:
        summary = f"type={type(body).__name__}"
    err = " [ERROR]" if envelope.get("result", {}).get("is_error") else ""
    print(f"  wrote {out.relative_to(REPO_ROOT)}  ({summary}){err}")
    return out


async def _call(session, step_idx: int, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    print(f"-> {tool}  args={json.dumps(arguments, default=str)[:160]}")
    raw = await asyncio.wait_for(
        session.call_tool(tool, arguments=arguments), timeout=60
    )
    return {
        "tool": tool,
        "arguments": arguments,
        "step_index": step_idx,
        "result": _payload(raw),
    }


def _walk_find(obj: Any, keys: tuple[str, ...]) -> Any:
    """Depth-first search for the first non-empty value under any of ``keys``."""
    if isinstance(obj, dict):
        for k in keys:
            val = obj.get(k)
            if val is not None and val != "" and val != []:
                return val
        for v in obj.values():
            found = _walk_find(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_find(v, keys)
            if found is not None:
                return found
    return None


def _sources(envelope: dict[str, Any]) -> list[Any]:
    """Yield structured (preferred) and prose data parts in priority order."""
    result = envelope.get("result", {})
    out: list[Any] = []
    structured = result.get("structured")
    if structured:
        out.append(structured)
    data = result.get("data")
    if data:
        out.append(data)
    return out


def _harvest_address_id(envelope: dict[str, Any]) -> str | None:
    for src in _sources(envelope):
        aid = _walk_find(src, ("addressId", "address_id", "id"))
        if aid:
            return str(aid).strip() or None
    return None


def _harvest_lat_lng(envelope: dict[str, Any]) -> tuple[float, float] | None:
    for src in _sources(envelope):
        lat = _walk_find(src, ("latitude", "lat"))
        lng = _walk_find(src, ("longitude", "lng", "lon"))
        try:
            if lat is not None and lng is not None:
                return float(lat), float(lng)
        except (TypeError, ValueError):
            continue
    return None


def _harvest_order_id(envelope: dict[str, Any]) -> str | None:
    for src in _sources(envelope):
        oid = _walk_find(src, ("orderId", "order_id"))
        if oid:
            return str(oid).strip() or None
    return None


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root = repo_root_from_settings()
    ensure_mcp_tokens_on_disk(root, mcp_url=MCP_URL, user_id=USER_ID)
    proxy = root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy.is_file():
        print("ERROR: mcp-remote not installed (npm install at repo root).", file=sys.stderr)
        sys.exit(2)

    params = StdioServerParameters(
        command="node",
        args=[str(proxy), MCP_URL],
        cwd=str(root),
        env=_stdio_env(root, user_id=USER_ID),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Instamart MCP   : {MCP_URL}")
    print(f"Output directory: {OUTPUT_DIR.relative_to(REPO_ROOT)}")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # Tool catalog snapshot (handy when the prompt drifts from the server).
            tools = await session.list_tools()
            catalog = [
                {
                    "name": t.name,
                    "description": (t.description or "").strip()[:500],
                    "input_schema": (t.inputSchema or {}),
                }
                for t in tools.tools
            ]
            (OUTPUT_DIR / "_instamart_tools_catalog.json").write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False)
            )
            print(f"  wrote outputs/instamart/_instamart_tools_catalog.json  (tools={len(catalog)})")

            step = 0

            # ---- get_addresses ----
            addr_env = await _call(session, step, "get_addresses", {})
            _write("get_addresses", addr_env)
            step += 1
            address_id = _harvest_address_id(addr_env)
            lat_lng = _harvest_lat_lng(addr_env)
            print(f"  harvested address_id={address_id}  lat_lng={lat_lng}")

            # ---- get_cart ----
            cart_env = await _call(session, step, "get_cart", {})
            _write("get_cart", cart_env)
            step += 1
            if not lat_lng:
                lat_lng = _harvest_lat_lng(cart_env)
                print(f"  harvested lat_lng from cart={lat_lng}")

            # ---- get_orders (active only) ----
            orders_active_env = await _call(session, step, "get_orders", {"activeOnly": True})
            _write("get_orders_active", orders_active_env)
            step += 1
            active_order_id = _harvest_order_id(orders_active_env)

            # ---- get_orders (all, default) ----
            orders_all_env = await _call(session, step, "get_orders", {})
            _write("get_orders_all", orders_all_env)
            step += 1
            if not active_order_id:
                active_order_id = _harvest_order_id(orders_all_env)
            print(f"  harvested order_id={active_order_id}")

            # ---- track_order (best-effort; only if we have an order + geo) ----
            if active_order_id and lat_lng:
                try:
                    track_env = await _call(
                        session,
                        step,
                        "track_order",
                        {
                            "orderId": active_order_id,
                            "lat": float(lat_lng[0]),
                            "lng": float(lat_lng[1]),
                        },
                    )
                    _write(f"track_order_{active_order_id[:10]}", track_env)
                    step += 1
                except Exception as e:
                    print(f"  track_order skipped ({type(e).__name__}: {e})")
            else:
                print("  track_order skipped (no order_id or lat_lng harvested)")

            # ---- search_products spread ----
            if address_id:
                for kw in SEARCH_KEYWORDS:
                    env = await _call(
                        session, step, "search_products",
                        {"addressId": address_id, "query": kw},
                    )
                    safe_kw = kw.replace(" ", "_")
                    _write(f"search_products_{safe_kw}", env)
                    step += 1

                # ---- your_go_to_items ----
                gtg_env = await _call(
                    session, step, "your_go_to_items",
                    {"addressId": address_id},
                )
                _write("your_go_to_items", gtg_env)
                step += 1
            else:
                print("  search_products / your_go_to_items skipped (no address_id)")

    print("\nDone. See outputs/instamart/ for raw envelopes.")


if __name__ == "__main__":
    asyncio.run(main())
