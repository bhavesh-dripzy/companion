"""Dump raw Kite (Zerodha) MCP responses for representative READ-ONLY tool calls.

Mirrors scripts/dump_zepto_outputs.py. Drives the live Kite MCP via
``mcp-remote`` (same OAuth tokens the executor uses) and writes each call as a
``{tool, arguments, step_index, result}`` envelope into ``outputs/kite/``.

**SAFETY — this is a real-money trading surface.** Every mutating / order /
GTT tool is HARD-BLOCKED here by an explicit denylist (``_MUTATING_RE``). The
script will refuse to call anything that looks like place/modify/cancel/delete
order or GTT, even if it appears in the catalog. The point of this script is to
capture response *shapes* for the planner-first refactor, never to trade.

What it does:
  1. ALWAYS dumps the full ``tools/list`` catalog → ``_kite_tools_catalog.json``.
     This is the single source of truth for the real tool names + input schemas;
     the executor/planner/prompt must agree with it.
  2. Best-effort calls a set of LIKELY read-only tools (names may differ — each
     is wrapped in try/except, so unknown names just no-op). After the catalog
     lands, retarget the names below to match exactly.

Usage:
    KITE_MCP_URL=https://mcp.kite.trade/mcp ./.venv/bin/python scripts/dump_kite_outputs.py

Prereq: complete the mcp-remote OAuth handshake once (Zerodha login) so a token
is cached under .mcp-auth/. Never commit tokens (.gitignore already excludes them).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "outputs" / "kite"

# Hard denylist: never call anything that could move money / place or change an
# order or GTT, regardless of what the catalog advertises.
_MUTATING_RE = re.compile(
    r"(place|modify|cancel|delete|create|exit|square[_-]?off|convert)",
    re.IGNORECASE,
)


def _is_mutating(tool: str) -> bool:
    return bool(_MUTATING_RE.search(tool or ""))


# Likely READ-ONLY tools (names per Kite Connect; confirm against the catalog).
# (tool_name, arguments). Each is best-effort.
READONLY_PROBES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("get_profile", {}),
    ("get_margins", {}),
    ("get_funds", {}),
    ("get_holdings", {}),
    ("get_positions", {}),
    ("get_orders", {}),
    ("get_trades", {}),
    ("get_gtts", {}),
)

# Instrument search keyword used to harvest a real (tradingsymbol, exchange,
# instrument_token) triple for the quote/LTP probes below.
SEARCH_QUERY = "RELIANCE"


def _payload(call_result: Any) -> dict[str, Any]:
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
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    out = OUTPUT_DIR / f"{safe}.json"
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
    if _is_mutating(tool):
        raise RuntimeError(f"refusing to call mutating tool {tool!r}")
    print(f"-> {tool}  args={json.dumps(arguments, default=str)[:160]}")
    raw = await asyncio.wait_for(session.call_tool(tool, arguments=arguments), timeout=60)
    return {"tool": tool, "arguments": arguments, "step_index": step_idx, "result": _payload(raw)}


def _walk_find(obj: Any, keys: tuple[str, ...]) -> Any:
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


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    proxy_script = REPO_ROOT / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        print("mcp-remote missing — run `npm install` at the repo root.", file=sys.stderr)
        sys.exit(1)

    mcp_url = os.environ.get("KITE_MCP_URL", "https://mcp.kite.trade/mcp")
    env = {**os.environ}
    env.setdefault("MCP_REMOTE_CONFIG_DIR", str(REPO_ROOT / ".mcp-auth/users/local_admin"))

    params = StdioServerParameters(
        command="node", args=[str(proxy_script), mcp_url], cwd=str(REPO_ROOT), env=env
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # --- 1. Tools catalog (the most important artifact) ----------
            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            catalog = {
                "ok": True,
                "tools": [
                    {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
                    for t in tools.tools
                ],
            }
            (OUTPUT_DIR / "_kite_tools_catalog.json").write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False)
            )
            names = [t["name"] for t in catalog["tools"]]
            print(f"wrote outputs/kite/_kite_tools_catalog.json ({len(names)} tools)")
            print("  catalog tools:", ", ".join(names))
            mutating = [n for n in names if _is_mutating(n)]
            if mutating:
                print("  MUTATING (will NOT be called):", ", ".join(mutating))

            available = set(names)
            step = 0

            # --- 2. Read-only account/portfolio/order probes -------------
            for tool, args in READONLY_PROBES:
                if tool not in available:
                    print(f"  (skip {tool}: not in catalog)")
                    continue
                try:
                    env_r = await _call(session, step, tool, args)
                    _write(tool, env_r)
                except Exception as e:  # noqa: BLE001
                    print(f"  ({tool} failed: {e})")
                step += 1

            # --- 3. Instrument search → harvest a real triple ------------
            harvested: dict[str, Any] = {}
            for search_tool in ("search_instruments", "search_instrument", "instruments_search"):
                if search_tool not in available:
                    continue
                try:
                    env_s = await _call(session, step, search_tool, {"query": SEARCH_QUERY})
                    _write(f"{search_tool}_{SEARCH_QUERY}", env_s)
                    body = env_s.get("result", {}).get("data")
                    harvested = {
                        "tradingsymbol": _walk_find(body, ("tradingsymbol", "trading_symbol", "symbol")),
                        "exchange": _walk_find(body, ("exchange",)),
                        "instrument_token": _walk_find(body, ("instrument_token", "instrumentToken", "token")),
                    }
                    print(f"  harvested: {harvested}")
                except Exception as e:  # noqa: BLE001
                    print(f"  ({search_tool} failed: {e})")
                step += 1
                break

            # --- 4. LTP / quote / ohlc for the harvested instrument ------
            sym = harvested.get("tradingsymbol")
            exch = harvested.get("exchange")
            inst = f"{exch}:{sym}" if exch and sym else "NSE:RELIANCE"
            for quote_tool in ("get_ltp", "get_quote", "get_ohlc"):
                if quote_tool not in available:
                    continue
                # Try a couple of common arg shapes; harmless if one errors.
                for args in ({"instruments": [inst]}, {"instruments": inst}, {"i": [inst]}):
                    try:
                        env_q = await _call(session, step, quote_tool, args)
                        _write(f"{quote_tool}", env_q)
                        break
                    except Exception as e:  # noqa: BLE001
                        print(f"  ({quote_tool} {args} failed: {e})")
                step += 1

            print("\nDONE. Mutating tools were NEVER called (hard denylist):")
            print("  place_order / modify_order / cancel_order / place_gtt / modify_gtt / delete_gtt / *")
            print("Next: open outputs/kite/_kite_tools_catalog.json to confirm real tool names + schemas.")


if __name__ == "__main__":
    asyncio.run(main())
