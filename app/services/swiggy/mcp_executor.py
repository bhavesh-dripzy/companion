"""
Swiggy MCP (Food + Instamart) tool execution via mcp-remote + stdio.

Kept separate from ``zomato_mcp_executor`` because address prefetch, response
shapes, and future filters differ (e.g. ``get_addresses`` vs
``get_saved_addresses_for_user``). Token storage and OAuth stay shared in the
Zomato module (mcp-remote uses the same ``MCP_REMOTE_CONFIG_DIR`` layout).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("SWIGGY_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("SWIGGY_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30")
)

_DEFAULT_SWIGGY_FOOD_MCP_URL = "https://mcp.swiggy.com/food"

# Extend Zomato placeholder aliases with Swiggy-shaped keys when learning IDs from tool output.
from app.services.zomato.mcp_executor import (  # noqa: E402
    _PLACEHOLDER_ALIASES as _BASE_PLACEHOLDER_ALIASES,
)
from app.services.zomato.mcp_executor import (  # noqa: E402
    _humanize_mcp_failure,
    _mcp_remote_config_dir,
    _mentions_placeholder,
    _stdio_env,
    _tool_result_payload,
    _unresolved_placeholders,
    _walk_json,
    repo_root_from_settings,
    substitute_placeholders,
)

_SWIGGY_RESTAURANT_EXTRA_ALIASES: tuple[str, ...] = (
    "swiggy_restaurant_id",
    "swiggyRestaurantId",
    "store_id",
    "storeId",
)

_SWIGGY_PLACEHOLDER_ALIASES: dict[str, tuple[str, ...]] = {
    **_BASE_PLACEHOLDER_ALIASES,
    "__RESTAURANT_ID__": _BASE_PLACEHOLDER_ALIASES["__RESTAURANT_ID__"] + _SWIGGY_RESTAURANT_EXTRA_ALIASES,
}


def _learn_swiggy_placeholders(values: dict[str, str], tool_payload: dict[str, Any]) -> None:
    data = tool_payload.get("data")
    for obj in _walk_json(data):
        if not isinstance(obj, dict):
            continue
        for placeholder, aliases in _SWIGGY_PLACEHOLDER_ALIASES.items():
            if placeholder in values:
                continue
            for alias in aliases:
                val = obj.get(alias)
                if val is None:
                    continue
                text = str(val).strip()
                if text:
                    values[placeholder] = text
                    break


def _first_address_id_swiggy(tool_payload: dict[str, Any]) -> str | None:
    """Resolve a delivery address id from Swiggy ``get_addresses`` (or similar) payloads."""
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
        for key in ("addresses", "user_addresses", "saved_addresses"):
            addresses = dd.get(key)
            if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict):
                row = addresses[0]
                aid = row.get("address_id") or row.get("addressId") or row.get("id")
                if aid is not None:
                    s = str(aid).strip()
                    if s:
                        return s
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        addresses = obj.get("addresses") or obj.get("user_addresses")
        if not (isinstance(addresses, list) and addresses and isinstance(addresses[0], dict)):
            continue
        aid = addresses[0].get("address_id") or addresses[0].get("addressId") or addresses[0].get("id")
        if aid is not None:
            s = str(aid).strip()
            if s:
                return s
    return None


def _learn_swiggy_restaurant_from_search(envelope: dict[str, Any], values: dict[str, str]) -> None:
    """Pick first restaurant id from search_restaurants (Swiggy uses ``id`` on each row)."""
    if "__RESTAURANT_ID__" in values:
        return
    if envelope.get("is_error"):
        return
    data = envelope.get("data")
    if not isinstance(data, dict):
        return
    rests = data.get("restaurants")
    if not (isinstance(rests, list) and rests and isinstance(rests[0], dict)):
        return
    rid = rests[0].get("id") or rests[0].get("restaurant_id") or rests[0].get("restaurantId")
    if rid is not None:
        s = str(rid).strip()
        if s:
            values["__RESTAURANT_ID__"] = s


_PREFETCH_ADDRESS_TOOLS = frozenset(
    {"get_addresses", "get_user_addresses", "get_saved_addresses_for_user"}
)


async def _run_swiggy_session_inner(
    tool_plan: list[dict[str, Any]],
    effective_address_id: str | None,
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as e:
        raise RuntimeError(
            "Python MCP SDK is not installed for this interpreter. Use Python 3.10+ "
            "and install requirements.txt, or run the Lambda Python 3.12 container."
        ) from e

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_address_id": effective_address_id,
        "executor": "swiggy.mcp_executor",
        "saved_address_prefetch_tool": "get_addresses",
    }

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError(
            "mcp-remote is not installed — run npm install at the repo root."
        )

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    resolved_id = (effective_address_id or "").strip() or None
    placeholder_values: dict[str, str] = {}
    if resolved_id:
        placeholder_values["__ADDRESS_ID__"] = resolved_id
    prefetch_done = False
    prefetch_tool = "get_addresses"

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(
                session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS
            )

            step_index = 0

            async def prefetch_addresses() -> None:
                nonlocal resolved_id, prefetch_done, step_index
                if prefetch_done:
                    return
                prefetch_done = True
                r = await asyncio.wait_for(
                    session.call_tool(prefetch_tool, arguments={}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                envelope = _tool_result_payload(r)
                step_results.append(
                    {
                        "tool": f"{prefetch_tool}(auto)",
                        "arguments": {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                nid = _first_address_id_swiggy(envelope)
                if nid:
                    resolved_id = nid
                    placeholder_values["__ADDRESS_ID__"] = nid
                    diagnostics["resolved_address_id"] = resolved_id
                else:
                    diagnostics["auto_address_failure"] = (
                        f"Could not extract address_id from {prefetch_tool} response."
                    )
                _learn_swiggy_placeholders(placeholder_values, envelope)
                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

            for raw in tool_plan:
                if not isinstance(raw, dict):
                    continue
                tool = (raw.get("tool") or "").strip()
                if not tool:
                    continue
                if tool in _PREFETCH_ADDRESS_TOOLS:
                    prefetch_done = True
                base_args = copy.deepcopy(raw.get("arguments") or {})
                if tool == "search_restaurants":
                    ba = dict(base_args)
                    if not str(ba.get("addressId") or "").strip() and not str(ba.get("address_id") or "").strip():
                        ba["addressId"] = "__ADDRESS_ID__"
                        ba["address_id"] = "__ADDRESS_ID__"
                    base_args = ba
                if tool == "search_menu":
                    ba = dict(base_args)
                    if not str(ba.get("addressId") or "").strip() and not str(ba.get("address_id") or "").strip():
                        ba["addressId"] = "__ADDRESS_ID__"
                        ba["address_id"] = "__ADDRESS_ID__"
                    base_args = ba

                exec_args = substitute_placeholders(copy.deepcopy(base_args), placeholder_values)

                if tool not in _PREFETCH_ADDRESS_TOOLS and _mentions_placeholder(exec_args):
                    unresolved = _unresolved_placeholders(exec_args)
                    if "__ADDRESS_ID__" in unresolved:
                        await prefetch_addresses()
                        exec_args = substitute_placeholders(
                            copy.deepcopy(base_args), placeholder_values
                        )

                if _mentions_placeholder(exec_args):
                    unresolved = _unresolved_placeholders(exec_args)
                    diagnostics.setdefault("skipped_tools", []).append(
                        {
                            "tool": tool,
                            "reason": "placeholder_unresolved",
                            "placeholders": unresolved,
                        }
                    )
                    continue

                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=exec_args or {}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
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

                if resolved_id is None and tool in _PREFETCH_ADDRESS_TOOLS:
                    nid = _first_address_id_swiggy(envelope)
                    if nid:
                        resolved_id = nid
                        placeholder_values["__ADDRESS_ID__"] = nid
                        diagnostics["resolved_address_id"] = resolved_id
                _learn_swiggy_placeholders(placeholder_values, envelope)
                if tool == "search_restaurants":
                    _learn_swiggy_restaurant_from_search(envelope, placeholder_values)
                diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())

    diagnostics["resolved_address_id"] = resolved_id
    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    return step_results, diagnostics


def run_swiggy_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Execute a Swiggy MCP tool plan (Food or Instamart URL)."""
    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("SWIGGY_FOOD_MCP_URL") or _DEFAULT_SWIGGY_FOOD_MCP_URL).strip()
    node = node_binary or os.getenv("NODE_BINARY", "node")

    cleaned: list[dict[str, Any]] = []
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
            _run_swiggy_session_inner(
                cleaned,
                address_id.strip() if address_id else None,
                root,
                url,
                node,
                user_id,
            )
        )
        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
    except BaseException as e:
        logger.exception("Swiggy MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e, url),
            "steps": [],
            "diagnostics": {
                "mcp_remote_config_dir": str(_mcp_remote_config_dir(root, user_id=user_id)),
            },
        }


def list_swiggy_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """List tools from a Swiggy MCP HTTP endpoint (same wire protocol as Zomato)."""
    from app.services.zomato.mcp_executor import list_mcp_tools_sync

    root = repo_root or repo_root_from_settings()
    url = (mcp_url or os.getenv("SWIGGY_FOOD_MCP_URL") or _DEFAULT_SWIGGY_FOOD_MCP_URL).strip()
    return list_mcp_tools_sync(
        repo_root=root,
        mcp_url=url,
        node_binary=node_binary,
        user_id=user_id,
    )
