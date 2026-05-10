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

import boto3

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

logger = logging.getLogger(__name__)

_PLACEHOLDER = "__ADDRESS_ID__"
_TOKEN_SECRET_ID = "companion/mcp-tokens"
_MCP_INIT_TIMEOUT_SECONDS = int(os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20"))
_MCP_TOOL_TIMEOUT_SECONDS = int(os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30"))


def _mcp_remote_config_dir(repo_root: Path) -> Path:
    """
    mcp-remote persists Zomato OAuth tokens under MCP_REMOTE_CONFIG_DIR
    (default: ~/.mcp-auth). On servers, www-data's home is not your laptop — set
    MCP_REMOTE_CONFIG_DIR to a project-owned directory and copy tokens there.
    """
    env = os.getenv("MCP_REMOTE_CONFIG_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (repo_root / ".mcp-auth").resolve()


def _stdio_env(repo_root: Path) -> dict[str, str]:
    cfg = _mcp_remote_config_dir(repo_root)
    try:
        cfg.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("could not create MCP_REMOTE_CONFIG_DIR %s: %s", cfg, e)
    base = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    base["MCP_REMOTE_CONFIG_DIR"] = str(cfg)
    return base


def _flatten_exception_group(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        nested: list[BaseException] = []
        for child in exc.exceptions:
            nested.extend(_flatten_exception_group(child))
        return nested
    return [exc]


def _humanize_mcp_failure(repo_root: Path, exc: BaseException) -> str:
    leaves = _flatten_exception_group(exc)
    parts = [f"{type(e).__name__}: {e}" for e in leaves] or [str(exc)]
    detail = "; ".join(parts)
    if any(isinstance(e, TimeoutError) for e in leaves):
        detail += (
            " | Zomato MCP timed out while initializing or calling a tool. "
            "This usually means OAuth/token setup is missing or the MCP server is slow."
        )
    if any("Connection closed" in str(e) for e in leaves) or any(
        type(e).__name__ == "McpError" for e in leaves
    ):
        cfg = _mcp_remote_config_dir(repo_root)
        detail += (
            " | Zomato MCP uses OAuth via mcp-remote. On the server, copy your "
            "~/.mcp-auth files from a machine where Zomato MCP already works in Cursor "
            f"into {cfg} (or set MCP_REMOTE_CONFIG_DIR), chown to www-data, then restart gunicorn."
        )
    return detail


def repo_root_from_settings() -> Path:
    try:
        from django.conf import settings

        return Path(settings.BASE_DIR)
    except Exception:
        return Path(__file__).resolve().parents[2]


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
        env=_stdio_env(repo_root),
    )

    resolved_id = (effective_address_id or "").strip() or None
    prefetch_done = False

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
                    session.call_tool("get_saved_addresses_for_user", arguments={}),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
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

                if resolved_id is None and tool == "get_saved_addresses_for_user":
                    nid = _first_address_id_from_saved_payload(envelope)
                    if nid:
                        resolved_id = nid
                        diagnostics["resolved_address_id"] = resolved_id

    diagnostics["resolved_address_id"] = resolved_id
    return step_results, diagnostics


# ---------------------------------------------------------------------------
# Token persistence (Lambda is ephemeral — store in Secrets Manager)
# ---------------------------------------------------------------------------


def _mcp_token_secret():
    """Return the raw JSON from companion/mcp-tokens, or {}."""
    try:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=_TOKEN_SECRET_ID)
        raw = resp.get("SecretString") or "{}"
        return json.loads(raw)
    except Exception:
        return {}


def restore_mcp_auth_tokens(repo_root: Path | None = None) -> bool:
    """Fetch OAuth tokens from Secrets Manager and write to MCP config dir.
    Returns True if tokens were found and restored."""
    root = repo_root or repo_root_from_settings()
    cfg_dir = _mcp_remote_config_dir(root)
    secret = _mcp_token_secret()
    files_data = secret.get("files") if isinstance(secret, dict) else None
    if not isinstance(files_data, dict) or not files_data:
        return False
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files_data.items():
        safe = os.path.basename(name)
        (cfg_dir / safe).write_text(str(content), encoding="utf-8")
    logger.info("Restored %d MCP auth token file(s) from Secrets Manager.", len(files_data))
    return True


def persist_mcp_auth_tokens(repo_root: Path | None = None) -> dict[str, str]:
    """Read OAuth tokens from MCP config dir and store in Secrets Manager.
    Returns the files that were persisted."""
    root = repo_root or repo_root_from_settings()
    cfg_dir = _mcp_remote_config_dir(root)
    files_data: dict[str, str] = {}
    if cfg_dir.is_dir():
        for p in cfg_dir.iterdir():
            if p.is_file():
                files_data[p.name] = p.read_text(encoding="utf-8")
    if files_data:
        try:
            client = boto3.client("secretsmanager")
            payload = json.dumps({"files": files_data, "updated_at": utc_now_iso()})
            client.put_secret_value(
                SecretId=_TOKEN_SECRET_ID,
                SecretString=payload,
            )
            logger.info("Persisted %d MCP auth token file(s) to Secrets Manager.", len(files_data))
        except client.exceptions.ResourceNotFoundException:
            client.create_secret(
                Name=_TOKEN_SECRET_ID,
                SecretString=payload,
                Description="Zomato MCP OAuth tokens for serverless Companion",
            )
            logger.info("Created MCP token secret with %d file(s).", len(files_data))
    return files_data


def get_mcp_token_status(repo_root: Path | None = None) -> dict[str, Any]:
    """Return a summary of MCP token state."""
    root = repo_root or repo_root_from_settings()
    cfg_dir = _mcp_remote_config_dir(root)
    local_files = (
        [p.name for p in cfg_dir.iterdir() if p.is_file()]
        if cfg_dir.is_dir() else []
    )
    secret = _mcp_token_secret()
    secret_files = list((secret.get("files") or {}).keys())
    return {
        "config_dir": str(cfg_dir),
        "local_files": local_files,
        "secret_files": secret_files,
        "has_local_tokens": len(local_files) > 0,
        "has_secret_tokens": len(secret_files) > 0,
    }


def capture_mcp_oauth_url(
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    """Try to detect Zomato MCP OAuth state via mcp-remote.
    
    Returns:
      - oauth_required: auth URL captured, user should open it
      - connected: mcp-remote connected (tokens present)
      - instructions: subprocess not viable (Lambda), return manual instructions
      - error: something went wrong
    """
    import shutil
    import subprocess
    import time
    
    root = repo_root or repo_root_from_settings()
    url = mcp_url or os.getenv("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node = node_binary or os.getenv("NODE_BINARY", "node")
    
    # Check if node exists
    node_path = shutil.which(node)
    if not node_path:
        return {
            "status": "instructions",
            "detail": (
                "Node.js not found in Lambda. To authorize Zomato MCP, run this on "
                "your local machine: npx mcp-remote https://mcp-server.zomato.com/mcp "
                "— then upload the resulting tokens from ~/.mcp-auth/ via the token form."
            ),
            "mcp_url": url,
        }
    
    proxy_script = root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        return {
            "status": "instructions",
            "detail": (
                "mcp-remote bridge not found in Lambda. Run this locally: "
                "npx mcp-remote https://mcp-server.zomato.com/mcp "
                "— then upload tokens from ~/.mcp-auth/."
            ),
            "mcp_url": url,
        }
    
    cfg = _mcp_remote_config_dir(root)
    
    try:
        cfg.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    
    stderr_lines: list[str] = []
    stdout_lines: list[str] = []
    auth_url: str | None = None
    
    try:
        proc = subprocess.Popen(
            [node_path, str(proxy_script), url],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "MCP_REMOTE_CONFIG_DIR": str(cfg)},
        )
        
        deadline = time.time() + timeout_seconds
        
        # Use a polling loop with non-blocking reads
        while time.time() < deadline:
            # Read available stderr
            import select
            try:
                rlist, _, _ = select.select([proc.stderr], [], [], 0.5)
                if proc.stderr in rlist:
                    chunk = proc.stderr.read(4096) or ""
                    if chunk:
                        stderr_lines.extend(chunk.splitlines(keepends=True))
                        for kw in ("authorization_url", "https://mcp-server.zomato.com/oauth", "https://accounts.zomato.com"):
                            if kw in chunk.lower().replace(" ", ""):
                                # Extract the URL
                                for line in chunk.splitlines():
                                    line_s = line.strip()
                                    if line_s.startswith("http") and "zomato" in line_s.lower():
                                        auth_url = line_s
                                        break
                                if auth_url:
                                    break
            except Exception:
                pass
            
            if auth_url:
                break
            
            # Read available stdout
            try:
                rlist, _, _ = select.select([proc.stdout], [], [], 0.1)
                if proc.stdout in rlist:
                    chunk = proc.stdout.read(4096) or ""
                    if chunk:
                        stdout_lines.extend(chunk.splitlines(keepends=True))
            except Exception:
                pass
            
            if proc.poll() is not None:
                break
        
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        
    except FileNotFoundError:
        return {
            "status": "instructions",
            "detail": f"Cannot spawn {node_path}. Run locally: npx mcp-remote {url} — then upload tokens.",
            "mcp_url": url,
        }
    except Exception as e:
        return {
            "status": "error",
            "detail": f"Subprocess error: {type(e).__name__}: {e}. Run locally: npx mcp-remote {url}",
            "mcp_url": url,
        }
    
    if auth_url:
        return {
            "status": "oauth_required",
            "authorization_url": auth_url,
            "detail": "Open this URL in your browser to authorize Zomato. Then upload tokens.",
            "mcp_url": url,
        }
    
    combined = "".join(stdout_lines + stderr_lines).lower()
    if any(kw in combined for kw in ("connected", "ready", "session established")):
        return {
            "status": "connected",
            "detail": "mcp-remote connected successfully. Tokens present.",
            "mcp_url": url,
        }
    
    return {
        "status": "instructions",
        "detail": (
            "Could not detect OAuth state automatically. Run this on your local machine: "
            "npx mcp-remote https://mcp-server.zomato.com/mcp "
            "— authorize in your browser — then upload tokens from ~/.mcp-auth/."
        ),
        "mcp_url": url,
        "stderr_tail": "".join(stderr_lines[-3:]) if stderr_lines else "",
    }


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# alias so the import in main.py resolves cleanly
utc_now_iso = _utc_now_iso


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
    except BaseException as e:
        logger.exception("Zomato MCP execution failed")
        return {
            "ok": False,
            "error_class": type(e).__name__,
            "error_detail": _humanize_mcp_failure(root, e),
            "steps": [],
            "diagnostics": {"mcp_remote_config_dir": str(_mcp_remote_config_dir(root))},
        }
