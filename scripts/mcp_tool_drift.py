"""Live MCP tool-catalog drift check.

Runs tools/list against every MCP provider with vaulted tokens and diffs the
result against the seed registry (core/registry/seed.py) — catching tools a
server added that we never registered (planner can't plan them) and seeded
tools a server dropped (plans silently fail). Also refreshes the committed
snapshot in docs/architecture/mcp-live-tool-catalog.json.

Usage:
    AWS_PROFILE=companion AWS_REGION=ap-south-1 \
        .venv/bin/python scripts/mcp_tool_drift.py [--user admin_bhavesh]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.providers.base import ExecCtx
from core.providers.mcp.connection import list_tools, mcp_session
from core.providers.plugins.district.provider import DistrictProvider
from core.providers.plugins.kite.provider import KiteProvider
from core.providers.plugins.swiggy_food.provider import SwiggyFoodProvider
from core.providers.plugins.swiggy_instamart.provider import SwiggyInstamartProvider
from core.providers.plugins.zepto.provider import ZeptoProvider
from core.providers.plugins.zomato.provider import ZomatoProvider
from core.registry.seed import _SPECS

_SNAPSHOT = Path(__file__).resolve().parents[1] / "docs/architecture/mcp-live-tool-catalog.json"

PROVIDERS = (
    ZomatoProvider(),
    SwiggyFoodProvider(),
    SwiggyInstamartProvider(),
    ZeptoProvider(),
    DistrictProvider(),
    KiteProvider(),
)


async def _discover(provider, user: str) -> list[dict]:
    spec = provider.stdio_spec(ExecCtx(user_id=user))
    async with mcp_session(spec) as session:
        raw = await list_tools(session)
    return [
        {
            "name": t["name"],
            "description": (t.get("description") or "")[:220],
            "params": sorted((t.get("input_schema") or {}).get("properties", {}).keys()),
            "required": (t.get("input_schema") or {}).get("required", []),
        }
        for t in raw
    ]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="admin_bhavesh")
    args = parser.parse_args()

    seeded = {pid: set(names.split()) for pid, _l, _a, names in _SPECS}
    snapshot: dict[str, dict] = {}
    drift = False
    for provider in PROVIDERS:
        try:
            tools = await asyncio.wait_for(_discover(provider, args.user), timeout=120)
        except Exception as exc:
            print(f"{provider.id}: DISCOVERY FAILED — {type(exc).__name__}: {exc}")
            drift = True
            continue
        snapshot[provider.id] = {"ok": True, "tools": tools}
        live = {t["name"] for t in tools}
        seed = seeded.get(provider.id, set())
        missing = sorted(live - seed)
        stale = sorted(seed - live)
        if missing or stale:
            drift = True
            print(f"{provider.id}: MISSING-FROM-SEED={missing} STALE-IN-SEED={stale}")
        else:
            print(f"{provider.id}: exact match ({len(live)} tools)")
    if snapshot:
        _SNAPSHOT.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        print(f"snapshot refreshed: {_SNAPSHOT}")
    print("DRIFT FOUND" if drift else "PARITY")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
