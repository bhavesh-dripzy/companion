"""mcp-remote connection layer (config dir, version, env, stdio spec, token hydration).

Ported from `zomato.mcp_executor`. Provider-agnostic: every hosted MCP provider
connects the same way, differing only by URL. The OAuth token *vault* hydration
(Secrets Manager -> on-disk, with mcp-remote's version-namespacing quirk) is the
one fragile, shared piece kept on the proven legacy path during migration; the
rest (config dir, version detection, env, command) is clean here.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from core.providers.mcp.connection import StdioSpec

_VERSION_RE = re.compile(r'var\s+version2\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']')


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _safe_user(user_id: str | None) -> str | None:
    if not user_id:
        return None
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", user_id.strip())
    return safe[:80] or None


def config_dir(user_id: str | None = None, root: Path | None = None) -> Path:
    """The MCP_REMOTE_CONFIG_DIR base (per-user), matching the legacy layout."""
    root = root or repo_root()
    safe = _safe_user(user_id)
    env = os.getenv("MCP_REMOTE_CONFIG_DIR", "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    elif os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        base = Path("/tmp/.mcp-auth").resolve()
    else:
        base = (root / ".mcp-auth").resolve()
    return (base / "users" / safe).resolve() if safe else base


def remote_version(root: Path | None = None) -> str | None:
    """The version mcp-remote uses to namespace its config dir (from the bundle)."""
    root = root or repo_root()
    pkg_root = root / "node_modules" / "mcp-remote"
    candidates: list[Path] = []
    with contextlib.suppress(OSError):
        candidates.extend(sorted((pkg_root / "dist").glob("chunk-*.js")))
    candidates.append(pkg_root / "dist" / "proxy.js")
    for js_path in candidates:
        try:
            match = _VERSION_RE.search(js_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if match:
            return match.group(1)
    return None


def stdio_env(user_id: str | None = None, root: Path | None = None) -> dict[str, str]:
    cfg = config_dir(user_id, root)
    with contextlib.suppress(OSError):
        cfg.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    env["MCP_REMOTE_CONFIG_DIR"] = str(cfg)
    return env


def hydrate_tokens(mcp_url: str, user_id: str | None = None, root: Path | None = None) -> None:
    """Restore OAuth tokens to disk before mcp-remote spawns (migration shim).

    Delegates to the proven legacy vault hydration; the vault is provider-agnostic
    and porting it cleanly is a separate, focused task."""
    with contextlib.suppress(Exception):
        from app.services.zomato.mcp_executor import ensure_mcp_tokens_on_disk

        ensure_mcp_tokens_on_disk(root or repo_root(), mcp_url=mcp_url, user_id=user_id)


def stdio_spec(mcp_url: str, *, user_id: str | None = None, root: Path | None = None) -> StdioSpec:
    """A ready-to-launch stdio spec for a hosted MCP server (tokens hydrated first)."""
    root = root or repo_root()
    hydrate_tokens(mcp_url, user_id=user_id, root=root)
    proxy = root / "node_modules" / "mcp-remote" / "dist" / "proxy.js"
    return StdioSpec(
        command=os.getenv("NODE_BINARY", "node"),
        args=[str(proxy), mcp_url],
        cwd=str(root),
        env=stdio_env(user_id, root),
    )
