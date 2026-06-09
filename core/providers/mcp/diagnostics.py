"""MCP environment diagnostics for the Settings status routes.

Ported from `app/services/zomato/mcp_executor.diagnose_mcp_environment`. Reports
whether Node + the mcp-remote bridge + the Python MCP SDK are present and the
current token state, so the UI can render an accurate per-provider status.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import Any

from core.providers.mcp.remote import (
    config_dir,
    effective_config_dir,
    remote_version,
    repo_root,
)
from core.providers.mcp.token_store import expected_token_paths, get_mcp_token_status

_DEFAULT_ZOMATO_MCP_URL = "https://mcp-server.zomato.com/mcp"


def diagnose_mcp_environment(
    repo_root_path: Path | None = None,
    *,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root_path or repo_root()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", _DEFAULT_ZOMATO_MCP_URL)
    node = node_binary or os.getenv("NODE_BINARY", "node")
    node_path = shutil.which(node)
    proxy_script = root / "node_modules" / "mcp-remote" / "dist" / "proxy.js"
    expected = expected_token_paths(root, url, user_id=user_id)
    return {
        "node_binary": node,
        "node_path": node_path,
        "node_available": bool(node_path),
        "mcp_remote_installed": proxy_script.is_file(),
        "mcp_remote_version": remote_version(root),
        "python_mcp_sdk_available": importlib.util.find_spec("mcp") is not None,
        "proxy_script": str(proxy_script),
        "config_dir": str(config_dir(user_id, root)),
        "effective_config_dir": str(effective_config_dir(user_id, root)),
        "expected_files": expected,
        "expected_tokens_present": Path(expected["tokens"]).is_file(),
        "token_status": get_mcp_token_status(root, mcp_url=url, user_id=user_id),
    }
