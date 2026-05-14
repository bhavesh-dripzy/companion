#!/usr/bin/env python3
"""
Live Swiggy Food MCP checks (same flows you exercise for Zomato: addresses → search → menu).

Prerequisites:
  - ``npm ci`` at repo root
  - ``.venv`` with ``pip install -r requirements.txt``
  - OAuth: ``bash scripts/swiggy_food_mcp_oauth.sh`` (tokens under ``.mcp-auth``)

Usage:
  cd /path/to/companion
  export MCP_REMOTE_CONFIG_DIR="$PWD/.mcp-auth"
  export ZOMATO_MCP_INIT_TIMEOUT_SECONDS=60
  .venv/bin/python scripts/test_swiggy_mcp_flows.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("MCP_REMOTE_CONFIG_DIR", str(ROOT / ".mcp-auth"))
    os.environ.setdefault("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "60")

    from app.services.swiggy_mcp_executor import (
        list_swiggy_mcp_tools_sync,
        run_swiggy_tool_plan_sync,
    )
    from app.services.zomato_mcp_executor import repo_root_from_settings, restore_mcp_auth_tokens

    root = repo_root_from_settings()
    url = os.environ.get("SWIGGY_FOOD_MCP_URL", "https://mcp.swiggy.com/food").strip()

    restore_mcp_auth_tokens(root, user_id=None)

    listed = list_swiggy_mcp_tools_sync(repo_root=root, mcp_url=url)
    print("list_tools ok:", listed.get("ok"), "mcp_url:", listed.get("mcp_url"))
    if not listed.get("ok"):
        print(json.dumps(listed, indent=2)[:4000])
        return 1
    names = [t.get("name") for t in listed.get("tools") or [] if isinstance(t, dict)]
    print("tool_count:", len(names))
    for n in ("get_addresses", "search_restaurants", "get_restaurant_menu"):
        print(f"  has {n}:", n in names)

    # 1) Saved addresses
    addr = run_swiggy_tool_plan_sync(
        [{"tool": "get_addresses", "arguments": {}}],
        mcp_url=url,
        repo_root=root,
    )
    print("\nget_addresses ok:", addr.get("ok"))
    if not addr.get("ok"):
        print(json.dumps(addr, indent=2)[:4000])
        return 1
    steps = addr.get("steps") or []
    if steps:
        print("first step:", steps[0].get("tool"))

    # 2) Restaurant search (optional address in args if API supports it)
    search = run_swiggy_tool_plan_sync(
        [{"tool": "search_restaurants", "arguments": {"query": "biryani"}}],
        mcp_url=url,
        repo_root=root,
    )
    print("\nsearch_restaurants ok:", search.get("ok"))
    if not search.get("ok"):
        print(json.dumps(search, indent=2)[:4000])
        return 1

    # 3) Menu using placeholder from search output
    menu_plan = [
        {"tool": "search_restaurants", "arguments": {"query": "pizza"}},
        {
            "tool": "get_restaurant_menu",
            "arguments": {"restaurant_id": "__RESTAURANT_ID__"},
        },
    ]
    menu = run_swiggy_tool_plan_sync(menu_plan, mcp_url=url, repo_root=root)
    print("\nsearch+menu plan ok:", menu.get("ok"), "steps:", len(menu.get("steps") or []))
    if not menu.get("ok"):
        print(json.dumps(menu, indent=2)[:6000])
        return 1

    print("\nAll Swiggy Food smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
