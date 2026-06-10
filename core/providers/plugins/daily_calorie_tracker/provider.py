"""Daily Calorie Tracker — local stdio MCP plugin (port of the legacy executor).

The orchestrator routes health_nutrition turns here, but the clean pipeline
never had the plugin, so every health turn dispatched to a ghost provider. The
server is the npm package `daily-calorie-tracker-mcp` run from node_modules
with a per-user HOME; its SQLite database is restored from / persisted to the
MCP token secret across Lambda cold starts (≤512KB).
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from core.contracts.tool import AuthMode
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ProviderResult
from core.providers.base import ExecCtx
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.mcp_base import McpProvider
from core.providers.mcp.remote import repo_root
from core.providers.mcp.token_store import mcp_token_secret, merge_mcp_token_secret

_PACKAGE_ENTRY = "node_modules/daily-calorie-tracker-mcp/dist/index.js"
_DB_SECRET_KEY = "daily_calorie_tracker_db_b64"
_MAX_DB_BYTES = 512 * 1024


def _safe_user(user_id: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", user_id or "default")[:64] or "default"


def _home(user_id: str | None) -> Path:
    return (
        Path("/tmp") / "companion-stdio-mcp" / _safe_user(user_id) / "daily-calorie-tracker"
    ).resolve()


def _db_path(home: Path) -> Path:
    return home / ".daily-calorie-tracker" / "calories.db"


def _restore_db(user_id: str | None, home: Path) -> None:
    """Hydrate the user's calorie DB from the secret (best-effort)."""
    try:
        payload = mcp_token_secret(user_id)
    except Exception:
        return
    encoded = payload.get(_DB_SECRET_KEY) if isinstance(payload, dict) else None
    if not isinstance(encoded, str) or not encoded:
        return
    try:
        raw = base64.b64decode(encoded.encode("ascii"))
    except Exception:
        return
    if len(raw) > _MAX_DB_BYTES:
        return
    db = _db_path(home)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(raw)


def _persist_db(user_id: str | None, home: Path) -> None:
    """Write the user's calorie DB back to the secret (best-effort)."""
    db = _db_path(home)
    if not db.is_file():
        return
    raw = db.read_bytes()
    if len(raw) > _MAX_DB_BYTES:
        return
    try:
        merge_mcp_token_secret(user_id, **{_DB_SECRET_KEY: base64.b64encode(raw).decode("ascii")})
    except Exception:
        return


class DailyCalorieTrackerProvider(McpProvider):
    id = "daily_calorie_tracker"
    label = "Daily Calorie Tracker MCP"
    auth_mode = AuthMode.TOOL

    def stdio_spec(self, ctx: ExecCtx) -> StdioSpec:
        root = repo_root()
        home = _home(ctx.user_id)
        home.mkdir(parents=True, exist_ok=True)
        env = {
            **{k: v for k, v in os.environ.items() if isinstance(v, str)},
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
        }
        return StdioSpec(command="node", args=[str(root / _PACKAGE_ENTRY)], cwd=str(root), env=env)

    async def run(self, calls: list[ToolCall], ctx: ExecCtx) -> ProviderResult:
        home = _home(ctx.user_id)
        _restore_db(ctx.user_id, home)
        result = await super().run(calls, ctx)
        _persist_db(ctx.user_id, home)
        return result
