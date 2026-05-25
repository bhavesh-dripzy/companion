"""Dump JSON schema for a District MCP tool (default: list all tools).

Usage:
    ./.venv/bin/python scripts/inspect_district_tool.py [tool_name|--all]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    repo_root = Path(__file__).resolve().parent.parent
    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        print("mcp-remote missing — run npm install first", file=sys.stderr)
        sys.exit(1)

    mcp_url = os.environ.get("DISTRICT_MCP_URL", "https://mcp-server.district.in/mcp")
    env = {**os.environ}
    env.setdefault(
        "MCP_REMOTE_CONFIG_DIR",
        str(repo_root / ".mcp-auth/users/local_admin"),
    )

    params = StdioServerParameters(
        command="node",
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=env,
    )

    tool_name = sys.argv[1] if len(sys.argv) > 1 else None

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            if tool_name is None or tool_name == "--all":
                for t in tools.tools:
                    desc = (t.description or "").strip().splitlines()[0] if t.description else ""
                    print(f"{t.name:40s}  {desc[:120]}")
                return
            for t in tools.tools:
                if t.name == tool_name:
                    print(f"=== {t.name} ===")
                    print("Description:")
                    print(t.description)
                    print()
                    print("Input schema:")
                    print(json.dumps(t.inputSchema, indent=2))
                    return
            print(f"tool {tool_name!r} not found; available:")
            for t in tools.tools:
                print(" -", t.name)


if __name__ == "__main__":
    asyncio.run(main())
