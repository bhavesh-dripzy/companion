from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Any

from app.services.zomato_mcp_executor import (
    _merge_mcp_token_secret,
    _mcp_token_secret,
    _safe_user_id,
    _tool_result_payload,
    repo_root_from_settings,
)

_INIT_TIMEOUT_SECONDS = 20
_TOOL_TIMEOUT_SECONDS = 30
_MAX_DB_BYTES = 512 * 1024


def _calorie_home(repo_root: Path, user_id: str | None) -> Path:
    safe = _safe_user_id(user_id) or "default"
    return (Path("/tmp") / "companion-stdio-mcp" / safe / "daily-calorie-tracker").resolve()


def _calorie_db_path(home: Path) -> Path:
    return home / ".daily-calorie-tracker" / "calories.db"


def _restore_calorie_db(user_id: str | None, home: Path) -> None:
    try:
        payload = _mcp_token_secret(user_id)
    except Exception:
        return
    encoded = payload.get("daily_calorie_tracker_db_b64") if isinstance(payload, dict) else None
    if not isinstance(encoded, str) or not encoded:
        return
    raw = base64.b64decode(encoded.encode("ascii"))
    if len(raw) > _MAX_DB_BYTES:
        return
    db_path = _calorie_db_path(home)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(raw)


def _persist_calorie_db(user_id: str | None, home: Path) -> None:
    db_path = _calorie_db_path(home)
    if not db_path.is_file():
        return
    raw = db_path.read_bytes()
    if len(raw) > _MAX_DB_BYTES:
        return
    try:
        _merge_mcp_token_secret(
            user_id,
            daily_calorie_tracker_db_b64=base64.b64encode(raw).decode("ascii"),
        )
    except Exception:
        return


async def _run_stdio_session(
    *,
    command: str,
    args: list[str],
    tool_plan: list[dict[str, Any]] | None,
    repo_root: Path,
    user_id: str | None,
    list_only: bool,
) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    home = _calorie_home(repo_root, user_id)
    home.mkdir(parents=True, exist_ok=True)
    _restore_calorie_db(user_id, home)

    env = {
        **{k: v for k, v in os.environ.items() if isinstance(v, str)},
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
    }
    params = StdioServerParameters(
        command=command,
        args=[str(repo_root / arg) if arg.startswith("node_modules/") else arg for arg in args],
        cwd=str(repo_root),
        env=env,
    )

    steps: list[dict[str, Any]] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT_SECONDS)
            if list_only:
                tools = await asyncio.wait_for(session.list_tools(), timeout=_TOOL_TIMEOUT_SECONDS)
                return {
                    "ok": True,
                    "tools": [
                        {
                            "name": tool.name,
                            "description": getattr(tool, "description", "") or "",
                            "input_schema": getattr(tool, "inputSchema", None),
                        }
                        for tool in tools.tools
                    ],
                }
            for step in tool_plan or []:
                tool = str(step.get("tool") or "").strip()
                if not tool:
                    continue
                arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=arguments),
                    timeout=_TOOL_TIMEOUT_SECONDS,
                )
                steps.append(
                    {
                        "tool": tool,
                        "arguments": arguments,
                        "result": _tool_result_payload(result),
                    }
                )
    _persist_calorie_db(user_id, home)
    return {"ok": True, "steps": steps, "diagnostics": {"home": str(home)}}


def run_stdio_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    command: str,
    args: list[str],
    repo_root: Path | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    try:
        return asyncio.run(
            _run_stdio_session(
                command=command,
                args=args,
                tool_plan=tool_plan,
                repo_root=root,
                user_id=user_id,
                list_only=False,
            )
        )
    except Exception as exc:
        return {
            "ok": False,
            "error_detail": f"{type(exc).__name__}: {exc}",
            "steps": [],
            "diagnostics": {"executor": "stdio_mcp_executor"},
        }


def list_stdio_mcp_tools_sync(
    *,
    command: str,
    args: list[str],
    repo_root: Path | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or repo_root_from_settings()
    try:
        return asyncio.run(
            _run_stdio_session(
                command=command,
                args=args,
                tool_plan=None,
                repo_root=root,
                user_id=user_id,
                list_only=True,
            )
        )
    except Exception as exc:
        return {"ok": False, "error_detail": f"{type(exc).__name__}: {exc}", "tools": []}
