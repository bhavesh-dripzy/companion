"""
Run Zomato MCP tool_plan via stdio bridge (mcp-remote + node), same stack as Cursor.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

logger = logging.getLogger(__name__)

_PLACEHOLDER = "__ADDRESS_ID__"


def repo_root_from_settings() -> Path:
    from django.conf import settings

    return Path(settings.BASE_DIR)


def substitute_address_id(obj: Any, address_id: str) -> Any:
    if obj == _PLACEHOLDER:
        return address_id
    if isinstance(obj, dict):
        return {k: substitute_address_id(v, address_id) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_address_id(i, address_id) for i in obj]
    return obj


def _mentions_placeholder(obj: Any) -> bool:
    if obj == _PLACEHOLDER:
        return True
    if isinstance(obj, dict):
        return any(_mentions_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_mentions_placeholder(i) for i in obj)
    return False


def _tool_result_payload(result: CallToolResult) -> dict[str, Any]:
    out: dict[str, Any] = {"is_error": bool(getattr(result, "isError", False))}
    try:
        if result.structuredContent is not None:
            out["data"] = result.structuredContent
            return out
        parsed: list[Any] = []
        for blk in getattr(result, "content", ()) or []:
            if getattr(blk, "type", None) == "text":
                txt = getattr(blk, "text", "") or ""
                try:
                    parsed.append(json.loads(txt))
                except json.JSONDecodeError:
                    parsed.append({"raw_text": txt})
            else:
                parsed.append({"type": getattr(blk, "type", "?"), "repr": str(blk)})
        out["data"] = parsed[0] if len(parsed) == 1 else {"blocks": parsed}
        return out
    except Exception as e:
        logger.warning("serialize tool result: %s", e)
        out["serialization_error"] = str(e)
        return out


def _first_address_id_from_saved_payload(tool_payload: dict[str, Any]) -> str | None:
    blob = tool_payload.get("data")
    if blob is None:
        return None
    objs: list[dict[str, Any]] = []
    if isinstance(blob, dict):
        objs.append(blob)
        r = blob.get("result")
        if isinstance(r, dict):
            objs.append(r)
    elif isinstance(blob, list):
        for entry in blob:
            if isinstance(entry, dict):
                objs.append(entry)
                rr = entry.get("result")
                if isinstance(rr, dict):
                    objs.append(rr)

    for dd in objs:
        addresses = dd.get("addresses")
        if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict):
            aid = addresses[0].get("address_id")
            if aid is not None:
                s = str(aid).strip()
                if s:
                    return s
    return None


async def _run_session_inner(
    tool_plan: list[dict[str, Any]],
    effective_address_id: str | None,
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {"requested_address_id": effective_address_id}

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError(
            "mcp-remote is not installed — run npm install at the repo root."
        )

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
    )

    resolved_id = (effective_address_id or "").strip() or None
    prefetch_done = False

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            step_index = 0

            async def prefetch_addresses() -> None:
                nonlocal resolved_id, prefetch_done, step_index
                if prefetch_done:
                    return
                prefetch_done = True
                r = await session.call_tool(
                    "get_saved_addresses_for_user", arguments={}
                )
                envelope = _tool_result_payload(r)
                step_results.append(
                    {
                        "tool": "get_saved_addresses_for_user(auto)",
                        "arguments": {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                nid = _first_address_id_from_saved_payload(envelope)
                if nid:
                    resolved_id = nid
                    diagnostics["resolved_address_id"] = resolved_id
                else:
                    diagnostics["auto_address_failure"] = (
                        "Could not extract address_id from Zomato response."
                    )

            for raw in tool_plan:
                if not isinstance(raw, dict):
                    continue
                tool = (raw.get("tool") or "").strip()
                if not tool:
                    continue
                if tool == "get_saved_addresses_for_user":
                    prefetch_done = True
                base_args = copy.deepcopy(raw.get("arguments") or {})

                if resolved_id:
                    exec_args = substitute_address_id(copy.deepcopy(base_args), resolved_id)
                else:
                    exec_args = base_args

                if tool != "get_saved_addresses_for_user" and _mentions_placeholder(
                    exec_args
                ):
                    await prefetch_addresses()
                    if resolved_id:
                        exec_args = substitute_address_id(
                            copy.deepcopy(base_args), resolved_id
                        )

                if _mentions_placeholder(exec_args):
                    diagnostics.setdefault("skipped_tools", []).append(
                        {"tool": tool, "reason": "address_id_unknown"}
                    )
                    continue

                if tool == "get_saved_addresses_for_user":
                    prefetch_done = True

                result = await session.call_tool(tool, arguments=exec_args or {})
                envelope = _tool_result_payload(result)
                step_results.append(
                    {
                        "tool": tool,
                        "arguments": exec_args or {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1

                if resolved_id is None and tool == "get_saved_addresses_for_user":
                    nid = _first_address_id_from_saved_payload(envelope)
                    if nid:
                        resolved_id = nid
                        diagnostics["resolved_address_id"] = resolved_id

    diagnostics["resolved_address_id"] = resolved_id
    return step_results, diagnostics


def run_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv(
        "ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp"
    )
    node = node_binary or os.getenv("NODE_BINARY", "node")

    cleaned = []
    for s in tool_plan:
        if isinstance(s, dict):
            tn = str(s.get("tool") or "").strip()
            if tn:
                cleaned.append(
                    {
                        "tool": tn,
                        "arguments": dict(s.get("arguments") or {}),
                    }
                )

    try:
        steps, diagnostics = asyncio.run(
            _run_session_inner(
                cleaned, address_id.strip() if address_id else None, root, url, node
            )
        )
        return {"ok": True, "steps": steps, "diagnostics": diagnostics}
    except Exception as e:
        logger.exception("Zomato MCP execution failed")
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": str(e),
            "steps": [],
            "diagnostics": {},
        }
