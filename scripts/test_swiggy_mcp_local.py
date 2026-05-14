#!/usr/bin/env python3
"""Exercise Companion Swiggy MCP wiring locally (diagnostics, tools/list, get_addresses).

Uses ``app.services.swiggy_mcp_executor`` only for execution/listing; token paths and
environment diagnostics still come from ``zomato_mcp_executor`` (shared mcp-remote store).

Usage from repo root (after venv + npm ci):
  MCP_REMOTE_CONFIG_DIR=.mcp-auth .venv/bin/python scripts/test_swiggy_mcp_local.py

If tools/list fails with OAuth, run:
  bash scripts/swiggy_food_mcp_oauth.sh
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SWIGGY_FOOD = os.environ.get("SWIGGY_FOOD_MCP_URL", "https://mcp.swiggy.com/food").strip()


def main() -> int:
    os.environ.setdefault("MCP_REMOTE_CONFIG_DIR", str(ROOT / ".mcp-auth"))
    os.environ.setdefault("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "120")
    os.chdir(ROOT)

    from app.services.swiggy_mcp_executor import (
        list_swiggy_mcp_tools_sync,
        run_swiggy_tool_plan_sync,
    )
    from app.services.zomato_mcp_executor import (
        _expected_token_paths,
        diagnose_mcp_environment,
        get_mcp_token_status,
        repo_root_from_settings,
    )

    root = repo_root_from_settings()
    print("repo_root:", root)
    print("mcp_url:", SWIGGY_FOOD)
    print("address_prefetch_tool (Swiggy): get_addresses")

    token_paths = _expected_token_paths(root, SWIGGY_FOOD, user_id=None)
    swiggy_token_file = Path(token_paths["tokens"])
    print("expected_swiggy_tokens_file:", swiggy_token_file)

    token = get_mcp_token_status(root, mcp_url=SWIGGY_FOOD, user_id=None)
    print("token_status:", json.dumps({k: token[k] for k in ("has_local_tokens", "has_secret_tokens")}, indent=2))

    if not swiggy_token_file.is_file():
        print(
            "\nNo Swiggy OAuth token file yet for this MCP URL (expected separate from Zomato).\n"
            "1) In a terminal on this machine (with a browser):\n"
            "     bash scripts/swiggy_food_mcp_oauth.sh\n"
            "   Complete login in the browser; wait until the script exits.\n"
            "2) Re-run:\n"
            f"     MCP_REMOTE_CONFIG_DIR={ROOT / '.mcp-auth'} .venv/bin/python scripts/test_swiggy_mcp_local.py\n",
            flush=True,
        )
        return 2

    diag = diagnose_mcp_environment(root, mcp_url=SWIGGY_FOOD, user_id=None)
    print(
        "diagnostics:",
        json.dumps(
            {k: diag[k] for k in ("node_available", "mcp_remote_installed", "python_mcp_sdk_available")},
            indent=2,
        ),
    )

    listed = list_swiggy_mcp_tools_sync(repo_root=root, mcp_url=SWIGGY_FOOD)
    if not listed.get("ok"):
        print("list_tools FAILED:", json.dumps(listed, indent=2)[:2000])
        print(
            "\nNext: complete OAuth so mcp-remote can write tokens for this URL hash.\n"
            "  bash scripts/swiggy_food_mcp_oauth.sh\n"
            "Then re-run this script.",
            flush=True,
        )
        return 1

    names = [t["name"] for t in listed.get("tools") or []]
    print(f"list_tools OK: {len(names)} tools")
    print("sample:", names[:15])

    expected = {"get_addresses", "search_restaurants"}
    missing = sorted(expected - set(names))
    if missing:
        print("WARNING: expected tools not in list:", missing)

    run = run_swiggy_tool_plan_sync(
        [{"tool": "get_addresses", "arguments": {}}],
        mcp_url=SWIGGY_FOOD,
        repo_root=root,
    )
    print("run_swiggy_tool_plan_sync address fetch ok:", run.get("ok"))
    steps = run.get("steps") or []
    if steps:
        r0 = (steps[0].get("result") or {})
        print("first step tool:", steps[0].get("tool"), "is_error:", r0.get("is_error"))

    if not run.get("ok"):
        print("address tool run:", json.dumps(run, indent=2)[:2500])
        return 1

    print("\nSwiggy Food MCP local check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
