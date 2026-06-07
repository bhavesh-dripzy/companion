"""Dump raw Zepto MCP responses for representative tool calls.

Mirrors the Zomato / Swiggy / District dump scripts. Drives the live Zepto
MCP via ``mcp-remote`` (same OAuth tokens the executor uses) and writes each
call as a ``{tool, arguments, step_index, result}`` envelope into
``outputs/zepto/``.

**Safety**: every mutating / money-handling tool is hard-skipped here. We
do not write to the cart, modify addresses, or place orders. The point of
this script is to feed the planner-first refactor with real response
shapes, not to drive a transaction.

Skipped on purpose (mutating):
- ``update_cart`` (writes to the user's cart)
- ``add_saved_address`` / ``select_saved_address`` / ``select_store``
  / ``update_drop_zone`` / ``update_user_name``
- ``create_order`` / ``create_online_payment_order``
  / ``create_upi_reserve_pay_order`` / ``create_wallet_order``
- ``check_payment_status`` (only meaningful after a real payment order;
  the tool description explicitly says do not call without prior context)

Read-only tools we dump (in roughly the order the planner will use them):
- ``get_user_details``
- ``list_saved_addresses``  (harvest a sample lat/lng + addressId)
- ``get_location_serviceability``  (uses harvested or default coords)
- ``get_past_order_items``  (Zepto recommends this as the first step
  before any search; we capture both the response and the side effect)
- ``list_order_history``  (harvest sample orderId)
- ``get_order_detail``  (with harvested orderId)
- ``view_cart``
- ``get_payment_methods``
- ``search_products`` for a spread of grocery keywords
- ``search_multiple_products`` for a small basket
- ``get_product_details`` (with harvested ``product_variant_id``)
- ``zepto_shop`` (the conversational entrypoint; one small probe)

Usage:
    ./.venv/bin/python scripts/dump_zepto_outputs.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Zepto tool responses are usually formatted prose (ChatGPT Apps style)
# with a footer like ``[1] pvid: <uuid>, spid: <uuid>`` or an embedded
# JSON blob containing ``"id": "<uuid>"``. The planner reads the prose;
# this dump script regex-extracts the same IDs so dependent calls
# (``get_product_details`` / ``get_order_detail``) work even though the
# top-level body is a string, not a dict.
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PVID_RE = re.compile(rf"pvid:\s*({_UUID_RE})", re.IGNORECASE)
_JSON_ID_RE = re.compile(rf'"id"\s*:\s*"({_UUID_RE})"')

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "outputs" / "zepto"

# Small spread of grocery queries that produce variety in the response
# envelope (dairy / bakery / snacks / protein / staples). The planner
# refactor will rely on these dumps to see what fields the catalog
# actually exposes.
SEARCH_KEYWORDS: tuple[str, ...] = (
    "milk",
    "bread",
    "biscuits",
    "eggs",
    "paneer",
    "atta",
)

MULTI_BASKET: tuple[str, ...] = ("milk", "bread", "eggs")

# Fallback location if the user has no saved addresses. Bangalore /
# Koramangala — known serviceable Zepto area.
DEFAULT_LAT = 12.9352
DEFAULT_LNG = 77.6245


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


async def _call(
    session, step_idx: int, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
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


def _harvest_str(envelope: dict[str, Any], *keys: str) -> str | None:
    body = envelope.get("result", {}).get("data")
    val = _walk_find(body, keys)
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _harvest_pvid(envelope: dict[str, Any]) -> str | None:
    """Pull the first ``pvid:`` UUID out of a Zepto search response."""
    body = envelope.get("result", {}).get("data")
    if not isinstance(body, str):
        return None
    m = _PVID_RE.search(body)
    return m.group(1) if m else None


def _harvest_order_id(envelope: dict[str, Any]) -> str | None:
    """Pull the first order ``id`` UUID from ``list_order_history`` prose."""
    body = envelope.get("result", {}).get("data")
    if not isinstance(body, str):
        return None
    m = _JSON_ID_RE.search(body)
    return m.group(1) if m else None


def _harvest_latlng(envelope: dict[str, Any]) -> tuple[float, float] | None:
    body = envelope.get("result", {}).get("data")
    lat = _walk_find(body, ("latitude", "lat"))
    lng = _walk_find(body, ("longitude", "lng", "lon"))
    try:
        if lat is not None and lng is not None:
            return float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    return None


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    proxy_script = REPO_ROOT / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        print("mcp-remote missing — run `npm install` at the repo root.", file=sys.stderr)
        sys.exit(1)

    mcp_url = os.environ.get("ZEPTO_MCP_URL", "https://mcp.zepto.co.in/mcp")
    env = {**os.environ}
    env.setdefault(
        "MCP_REMOTE_CONFIG_DIR",
        str(REPO_ROOT / ".mcp-auth/users/local_admin"),
    )

    params = StdioServerParameters(
        command="node",
        args=[str(proxy_script), mcp_url],
        cwd=str(REPO_ROOT),
        env=env,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # --- 1. Tools catalog --------------------------------------
            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            catalog = {
                "ok": True,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema,
                    }
                    for t in tools.tools
                ],
            }
            (OUTPUT_DIR / "_zepto_tools_catalog.json").write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False)
            )
            print(
                f"wrote outputs/zepto/_zepto_tools_catalog.json ({len(catalog['tools'])} tools)"
            )

            step = 0

            # --- 2. User profile --------------------------------------
            try:
                env_user = await _call(session, step, "get_user_details", {})
                _write("get_user_details", env_user)
            except Exception as e:  # noqa: BLE001
                print(f"  (get_user_details failed: {e})")
            step += 1

            # --- 3. Saved addresses -----------------------------------
            # We only harvest coordinates here (for the serviceability
            # call below). The address-id footer is text-only and the
            # planner will read it directly from the prose — no need to
            # extract it for the dump.
            harvested_lat: float | None = None
            harvested_lng: float | None = None
            try:
                env_addr = await _call(session, step, "list_saved_addresses", {})
                _write("list_saved_addresses", env_addr)
                ll = _harvest_latlng(env_addr)
                if ll:
                    harvested_lat, harvested_lng = ll
            except Exception as e:  # noqa: BLE001
                print(f"  (list_saved_addresses failed: {e})")
            step += 1

            # --- 4. Location serviceability ---------------------------
            lat = harvested_lat if harvested_lat is not None else DEFAULT_LAT
            lng = harvested_lng if harvested_lng is not None else DEFAULT_LNG
            try:
                env_serv = await _call(
                    session,
                    step,
                    "get_location_serviceability",
                    {"latitude": lat, "longitude": lng},
                )
                _write(f"get_location_serviceability_{lat:.4f}_{lng:.4f}", env_serv)
            except Exception as e:  # noqa: BLE001
                print(f"  (get_location_serviceability failed: {e})")
            step += 1

            # --- 5. Past order items (Zepto's recommended priming call) ---
            try:
                env_past = await _call(session, step, "get_past_order_items", {})
                _write("get_past_order_items", env_past)
            except Exception as e:  # noqa: BLE001
                print(f"  (get_past_order_items failed: {e})")
            step += 1

            # --- 6. Order history + first order detail ---------------
            harvested_order_id: str | None = None
            try:
                env_history = await _call(
                    session, step, "list_order_history", {"limit": 5, "pageNumber": 1}
                )
                _write("list_order_history", env_history)
                harvested_order_id = _harvest_order_id(env_history) or _harvest_str(
                    env_history, "orderId", "order_id", "id"
                )
            except Exception as e:  # noqa: BLE001
                print(f"  (list_order_history failed: {e})")
            step += 1

            if harvested_order_id:
                try:
                    env_od = await _call(
                        session,
                        step,
                        "get_order_detail",
                        {"orderId": harvested_order_id},
                    )
                    _write(f"get_order_detail_{harvested_order_id}", env_od)
                except Exception as e:  # noqa: BLE001
                    print(f"  (get_order_detail failed: {e})")
                step += 1
            else:
                print("  (no orderId harvested — skipping get_order_detail)")

            # --- 7. Cart + payment methods (read-only) ---------------
            try:
                env_cart = await _call(session, step, "view_cart", {})
                _write("view_cart", env_cart)
            except Exception as e:  # noqa: BLE001
                print(f"  (view_cart failed: {e})")
            step += 1

            try:
                env_pm = await _call(session, step, "get_payment_methods", {})
                _write("get_payment_methods", env_pm)
            except Exception as e:  # noqa: BLE001
                print(f"  (get_payment_methods failed: {e})")
            step += 1

            # --- 8. Searches -----------------------------------------
            harvested_variant_id: str | None = None
            for kw in SEARCH_KEYWORDS:
                try:
                    env_search = await _call(
                        session, step, "search_products", {"query": kw}
                    )
                    _write(f"search_products_{kw}", env_search)
                    if not harvested_variant_id:
                        harvested_variant_id = _harvest_pvid(env_search) or _harvest_str(
                            env_search,
                            "product_variant_id",
                            "productVariantId",
                            "variantId",
                            "variant_id",
                        )
                except Exception as e:  # noqa: BLE001
                    print(f"  (search_products[{kw}] failed: {e})")
                step += 1

            try:
                env_multi = await _call(
                    session,
                    step,
                    "search_multiple_products",
                    {"queries": list(MULTI_BASKET)},
                )
                _write(
                    "search_multiple_products_" + "_".join(MULTI_BASKET), env_multi
                )
                if not harvested_variant_id:
                    harvested_variant_id = _harvest_pvid(env_multi) or _harvest_str(
                        env_multi,
                        "product_variant_id",
                        "productVariantId",
                        "variantId",
                        "variant_id",
                    )
            except Exception as e:  # noqa: BLE001
                print(f"  (search_multiple_products failed: {e})")
            step += 1

            # --- 9. Product details ----------------------------------
            if harvested_variant_id:
                try:
                    env_pd = await _call(
                        session,
                        step,
                        "get_product_details",
                        {"product_variant_id": harvested_variant_id},
                    )
                    _write(
                        f"get_product_details_{harvested_variant_id}", env_pd
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"  (get_product_details failed: {e})")
                step += 1
            else:
                print("  (no product_variant_id harvested — skipping get_product_details)")

            # --- 10. zepto_shop conversational probe -----------------
            try:
                env_shop = await _call(
                    session,
                    step,
                    "zepto_shop",
                    {"intent": "search", "query": "milk", "quantity": 1},
                )
                _write("zepto_shop_search_milk", env_shop)
            except Exception as e:  # noqa: BLE001
                print(f"  (zepto_shop failed: {e})")
            step += 1

            print("\nDONE. Skipped on purpose (mutating / money-handling):")
            print("  - update_cart")
            print("  - add_saved_address / select_saved_address / select_store")
            print("  - update_drop_zone / update_user_name")
            print("  - create_order / create_online_payment_order")
            print("  - create_upi_reserve_pay_order / create_wallet_order")
            print("  - check_payment_status")


if __name__ == "__main__":
    asyncio.run(main())
