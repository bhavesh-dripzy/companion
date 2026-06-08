"""MCP provider management routes (status / OAuth / pairing / test / tools) + helpers.

Extracted from main.py (#29). Self-contained: the MCP helpers, the pairing model,
the 12 /api/mcp/* routes, and /api/food/saved-addresses/ (which is MCP-backed).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from app.services.food_response_builder import saved_address_label_from_mcp_item
from app.services.tool_runtime import (
    get_tool_provider,
    list_tool_providers,
    update_tool_provider_tools,
)
from companion_api.auth import get_current_user
from companion_api.secrets import secret_or_env
from companion_api.web_utils import oauth_result_page as _oauth_result_page
from companion_api.web_utils import public_url as _public_url

logger = logging.getLogger(__name__)
router = APIRouter()


def _pairing_script(*, base_url: str, code: str, mcp_url: str, mcp_remote_version: str) -> str:
    return f"""#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

API_BASE = {base_url!r}
PAIRING_CODE = {code!r}
MCP_URL = {mcp_url!r}
MCP_REMOTE_VERSION = {mcp_remote_version!r}
TIMEOUT_SECONDS = 240
MAX_FILE_BYTES = 256 * 1024

def fail(message):
    print("Companion MCP pairing failed: " + message, file=sys.stderr)
    sys.exit(1)

if shutil.which("npx") is None:
    fail("npx was not found. Install Node.js 20+ and retry.")

config_dir = tempfile.mkdtemp(prefix="companion-mcp-auth-")
env = os.environ.copy()
env["MCP_REMOTE_CONFIG_DIR"] = config_dir
cmd = [
    "npx",
    "-y",
    "mcp-remote@" + MCP_REMOTE_VERSION,
    MCP_URL,
    "--auth-timeout",
    str(TIMEOUT_SECONDS),
]

print("Starting MCP authorization through mcp-remote.")
print("A browser window should open. Complete provider sign-in, then return here.")
proc = subprocess.Popen(
    cmd,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=env,
)

def stream_output():
    if not proc.stdout:
        return
    for line in proc.stdout:
        print(line.rstrip())

threading.Thread(target=stream_output, daemon=True).start()

token_seen = False
deadline = time.time() + TIMEOUT_SECONDS
while time.time() < deadline:
    for root, _, names in os.walk(config_dir):
        if any(name.endswith("_tokens.json") for name in names):
            token_seen = True
            break
    if token_seen:
        time.sleep(2)
        break
    if proc.poll() is not None:
        time.sleep(1)
    time.sleep(0.2)

if proc.poll() is None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

if not token_seen:
    fail("No mcp-remote token file appeared. Complete the browser authorization and run the command again.")

files = {{}}
for root, _, names in os.walk(config_dir):
    for name in names:
        if name.endswith("_debug.log") or name == ".DS_Store":
            continue
        path = os.path.join(root, name)
        if os.path.getsize(path) > MAX_FILE_BYTES:
            continue
        rel = os.path.relpath(path, config_dir).replace(os.sep, "/")
        with open(path, "r", encoding="utf-8") as handle:
            files[rel] = handle.read()

payload = json.dumps({{"code": PAIRING_CODE, "files": files}}).encode("utf-8")
request = urllib.request.Request(
    API_BASE + "/api/mcp/pairing/complete",
    data=payload,
    headers={{"Content-Type": "application/json", "Accept": "application/json"}},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    fail("server rejected the pairing: " + body)
except Exception as exc:
    fail(type(exc).__name__ + ": " + str(exc))

print("Companion MCP pairing complete.")
print(body)
"""


class McpPairingComplete(BaseModel):
    code: str
    files: dict[str, str]


def _provider_mcp_url(provider_id: str, user_id: str | None = None) -> tuple[Any, str]:
    tool_provider = get_tool_provider(provider_id, user_id=user_id) or get_tool_provider(
        "zomato", user_id=user_id
    )
    if tool_provider and tool_provider.stdio_command:
        return tool_provider, ""
    mcp_url = secret_or_env(
        tool_provider.mcp_url_env
        if tool_provider and tool_provider.mcp_url_env
        else "ZOMATO_MCP_URL",
        tool_provider.default_mcp_url
        if tool_provider and tool_provider.default_mcp_url
        else "https://mcp-server.zomato.com/mcp",
    )
    return tool_provider, mcp_url


def _extract_saved_addresses_from_mcp_step(step: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize saved-address rows from an MCP tools/call step (Zomato, Swiggy, etc.)."""
    rows: list[dict[str, str]] = []
    res = step.get("result") if isinstance(step, dict) else None
    if not isinstance(res, dict) or res.get("is_error"):
        return rows
    payload = res.get("data")
    candidates: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("addresses"), list):
            candidates.extend(payload["addresses"])
        inner = payload.get("result")
        if isinstance(inner, dict) and isinstance(inner.get("addresses"), list):
            candidates.extend(inner["addresses"])
        data_inner = payload.get("data")
        if isinstance(data_inner, dict) and isinstance(data_inner.get("addresses"), list):
            candidates.extend(data_inner["addresses"])
    for item in candidates:
        if not isinstance(item, dict):
            continue
        aid = item.get("address_id") or item.get("addressId") or item.get("id")
        if aid is None:
            continue
        sid = str(aid).strip()
        if not sid:
            continue
        label = saved_address_label_from_mcp_item(item) or sid
        rows.append({"address_id": sid, "location_name": str(label).strip() or sid})
    return rows


def _compute_mcp_status(provider: str, user: str) -> dict[str, Any]:
    """Compute the MCP status for a single provider.

    Auto-restores OAuth tokens from Secrets Manager on cold Lambdas or after
    a browser hard-refresh so the UI can show "Connected" the moment the
    user opens Settings, instead of momentarily flashing "ready_pending".
    """
    from app.services.zomato_mcp_executor import (
        diagnose_mcp_environment,
        get_mcp_token_status,
        repo_root_from_settings,
        restore_mcp_auth_tokens,
    )

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if tool_provider and not tool_provider.available:
        return {
            "provider": tool_provider.name,
            "provider_label": tool_provider.label,
            "status": "provider_unavailable",
            "detail": tool_provider.notes or "This MCP provider is not configured yet.",
            "mcp_url": None,
            "node_binary": None,
            "has_local_tokens": False,
            "has_secret_tokens": False,
            "token_status": {},
            "diagnostics": {},
        }
    node_binary = secret_or_env("NODE_BINARY", "node")
    if tool_provider and tool_provider.stdio_command:
        command_path = (
            repo_root_from_settings() / tool_provider.stdio_args[0]
            if tool_provider.stdio_args
            else None
        )
        installed = bool(command_path and command_path.is_file())
        return {
            "provider": tool_provider.name,
            "provider_label": tool_provider.label,
            "status": "ready" if installed else "missing_bridge",
            "detail": (
                f"{tool_provider.label} is installed and ready."
                if installed
                else f"{tool_provider.label} package is not installed in node_modules."
            ),
            "mcp_url": None,
            "node_binary": node_binary,
            "has_local_tokens": True,
            "has_secret_tokens": False,
            "token_status": {"auth_mode": "tool", "transport": "stdio"},
            "diagnostics": {
                "transport": "stdio",
                "command": tool_provider.stdio_command,
                "args": list(tool_provider.stdio_args),
                "installed": installed,
            },
        }
    url_set = bool(mcp_url.strip())

    root = repo_root_from_settings()
    token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)
    has_local = token_status["has_local_tokens"]
    has_secret = token_status["has_secret_tokens"]

    # On cold Lambdas (or after a browser hard refresh) the ephemeral
    # /tmp/.mcp-auth is empty even when tokens are durably persisted in
    # Secrets Manager. Restore them eagerly so the UI shows "Connected"
    # immediately — without this, every provider that was paired during
    # a previous warm container would appear disconnected until the next
    # MCP call manually triggered the restore.
    if not has_local and has_secret:
        try:
            restore_mcp_auth_tokens(root, user_id=user)
            token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)
            has_local = token_status["has_local_tokens"]
            has_secret = token_status["has_secret_tokens"]
        except Exception:
            logger.exception("Auto-restore of MCP tokens failed for provider=%s", provider)

    diagnostics = diagnose_mcp_environment(
        root, mcp_url=mcp_url, node_binary=node_binary, user_id=user
    )
    url_authenticated = bool(tool_provider and tool_provider.auth_mode == "url")

    if not url_set:
        status = "not_configured"
        detail = f"{tool_provider.label if tool_provider else 'MCP'} URL is not set."
    elif not diagnostics["node_available"]:
        status = "missing_node"
        detail = f"Node.js is not available for NODE_BINARY={node_binary!r}. Install Node.js or set NODE_BINARY."
    elif not diagnostics["mcp_remote_installed"]:
        status = "missing_bridge"
        detail = (
            "mcp-remote is not installed. Run npm install at the repo root, then refresh status."
        )
    elif not diagnostics["python_mcp_sdk_available"]:
        status = "missing_python_sdk"
        detail = (
            "The Python MCP SDK is not installed for this interpreter. This local shell is likely "
            "using Python 3.9; use Python 3.10+ or the Lambda Python 3.12 runtime, then install requirements."
        )
    elif url_authenticated:
        status = "ready"
        detail = "MCP URL is configured. This provider authenticates through the server URL."
    elif has_local:
        status = "ready"
        detail = "OAuth tokens are present in the mcp-remote token store."
    elif has_secret:
        status = "ready_pending"
        detail = "Tokens are stored in Secrets Manager and will restore on the next MCP call."
    else:
        status = "needs_tokens"
        detail = (
            "MCP URL and bridge are configured, but no OAuth token file was found. "
            "Use Connect to generate a one-time local pairing command."
        )

    return {
        "provider": tool_provider.name if tool_provider else "zomato",
        "provider_label": tool_provider.label if tool_provider else "Zomato MCP",
        "status": status,
        "detail": detail,
        "mcp_url": mcp_url if url_set else None,
        "node_binary": node_binary,
        "has_local_tokens": has_local,
        "has_secret_tokens": has_secret,
        "token_status": token_status,
        "diagnostics": diagnostics,
    }


@router.get("/api/mcp/status/")
def mcp_status(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    return _compute_mcp_status(provider, user)


@router.post("/api/mcp/disconnect/")
def mcp_disconnect(
    provider: str = Query(...),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Forget every credential for a single MCP provider.

    Wipes both the durable Secrets Manager copy and the ephemeral Lambda
    /tmp copy of *only* the chosen provider's OAuth files. Other providers
    the user has paired (Zomato, Swiggy, District, ...) keep their tokens
    — disconnecting one MUST NOT log them out of the others.
    """
    from app.services.zomato_mcp_executor import (
        _mcp_remote_config_dir,
        _mcp_token_secret,
        _merge_mcp_token_secret,
        _server_url_hash,
        repo_root_from_settings,
    )

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider:
        raise HTTPException(status_code=404, detail=f"Unknown MCP provider {provider!r}.")
    if not mcp_url:
        raise HTTPException(
            status_code=400,
            detail=f"{tool_provider.label} has no MCP URL configured; nothing to disconnect.",
        )

    url_hash = _server_url_hash(mcp_url)
    root = repo_root_from_settings()

    # 1. Strip the provider's files from the durable Secrets Manager copy.
    #    mcp-remote names files `<md5(mcp_url)>_<kind>` so filtering by the
    #    hash leaves every other provider's tokens untouched.
    secret = _mcp_token_secret(user)
    files = secret.get("files") if isinstance(secret, dict) else None
    removed_secret: list[str] = []
    if isinstance(files, dict) and files:
        kept: dict[str, str] = {}
        for key, value in files.items():
            if url_hash in str(key):
                removed_secret.append(str(key))
            else:
                kept[str(key)] = value
        if removed_secret:
            _merge_mcp_token_secret(user, files=kept)

    # 2. Delete the same files from the live Lambda /tmp (or local dev dir).
    cfg_dir = _mcp_remote_config_dir(root, user_id=user)
    removed_local: list[str] = []
    if cfg_dir.is_dir():
        for path in cfg_dir.rglob("*"):
            if not path.is_file():
                continue
            if url_hash in path.name:
                try:
                    path.unlink()
                    removed_local.append(path.name)
                except OSError:
                    logger.exception("Failed to delete MCP token file %s", path)

    return {
        "status": "disconnected",
        "provider": tool_provider.name,
        "provider_label": tool_provider.label,
        "removed_secret_files": removed_secret,
        "removed_local_files": removed_local,
        "detail": (
            f"{tool_provider.label} credentials cleared."
            if (removed_secret or removed_local)
            else f"{tool_provider.label} had no stored credentials to remove."
        ),
    }


@router.get("/api/mcp/status/all/")
def mcp_status_all(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Return the connection status for every tool provider in one call.

    Lets the Settings UI render a "Connected" badge next to every provider
    without firing N separate requests on modal open (which made earlier
    pairings briefly appear disconnected during the round-trip).
    """
    providers = list_tool_providers(user_id=user)
    statuses: list[dict[str, Any]] = []
    for entry in providers:
        provider_id = str(entry.get("id") or "").strip()
        if not provider_id:
            continue
        try:
            status_payload = _compute_mcp_status(provider_id, user)
        except Exception as exc:
            logger.exception("mcp_status_all failed for provider=%s", provider_id)
            status_payload = {
                "provider": provider_id,
                "provider_label": entry.get("label") or provider_id,
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
                "has_local_tokens": False,
                "has_secret_tokens": False,
            }
        status_payload.setdefault("provider", provider_id)
        status_payload.setdefault("provider_label", entry.get("label") or provider_id)
        statuses.append(status_payload)
    return {"statuses": statuses}


@router.post("/api/mcp/oauth/")
def mcp_initiate_oauth(
    request: Request,
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    """Start a hosted OAuth flow for an MCP provider.

    This avoids mcp-remote's localhost browser callback, which is unsuitable for
    Lambda/API Gateway. The callback stores mcp-remote-compatible token files in
    Secrets Manager and /tmp for the current warm Lambda instance.
    """
    from app.services.zomato_mcp_executor import (
        repo_root_from_settings,
        start_web_oauth,
    )

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider or not tool_provider.available:
        raise HTTPException(
            status_code=400, detail=f"Provider '{provider}' is not available or not configured."
        )
    if tool_provider.auth_mode in {"url", "tool"} or tool_provider.stdio_command:
        return {
            "status": "not_required",
            "detail": f"{tool_provider.label} does not use Companion-hosted OAuth. Use Test to verify tools.",
            "provider": provider,
            "provider_label": tool_provider.label,
            "mcp_url": mcp_url,
        }
    callback_url = _public_url(request, "/api/mcp/oauth/callback")
    try:
        result = start_web_oauth(
            repo_root=repo_root_from_settings(),
            mcp_url=mcp_url,
            callback_url=callback_url,
            user_id=user,
            provider_label=tool_provider.label,
        )
        result["provider"] = provider
        result["provider_label"] = tool_provider.label
        return result
    except Exception as e:
        return {
            "status": "oauth_unavailable",
            "detail": f"{type(e).__name__}: {e}",
            "mcp_url": mcp_url,
        }


@router.post("/api/mcp/pairing/")
def mcp_pairing(
    request: Request,
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from app.services.zomato_mcp_executor import create_pairing_session, repo_root_from_settings

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if tool_provider and not tool_provider.available:
        return {
            "status": "provider_unavailable",
            "detail": tool_provider.notes or "This MCP provider is not configured yet.",
            "mcp_url": None,
        }
    if tool_provider and (tool_provider.auth_mode == "tool" or tool_provider.stdio_command):
        return {
            "status": "not_required",
            "detail": f"{tool_provider.label} does not use Companion pairing. Use Test to verify tools.",
            "mcp_url": mcp_url or None,
        }
    base_url = _public_url(request, "").rstrip("/")
    session = create_pairing_session(
        repo_root=repo_root_from_settings(), mcp_url=mcp_url, user_id=user
    )
    script_url = f"{base_url}/api/mcp/pairing/script?code={quote(session['code'])}"
    return {
        "status": "pairing_ready",
        "detail": (
            f"Run this command on the laptop where your browser can sign into {tool_provider.label if tool_provider else 'the MCP'}. "
            "It opens the provider through mcp-remote and syncs the session back automatically."
        ),
        "command": f"curl -fsSL '{script_url}' | python3",
        "expires_in_seconds": session["expires_in_seconds"],
        "mcp_url": mcp_url,
    }


@router.get("/api/mcp/pairing/script", response_class=PlainTextResponse)
def mcp_pairing_script(request: Request, code: str = Query(...)):
    base_url = _public_url(request, "").rstrip("/")
    version = "0.1.38"
    try:
        from app.services.zomato_mcp_executor import (
            _mcp_remote_version,
            find_pairing_session_info,
            repo_root_from_settings,
        )

        version = _mcp_remote_version(repo_root_from_settings()) or version
        session = find_pairing_session_info(code)
        if not session:
            return "# Pairing code expired. Generate a new Connect command from Companion.\n"
        mcp_url = str(session.get("mcp_url") or "").strip()
        version = str(session.get("mcp_remote_version") or version)
    except Exception:
        mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    return _pairing_script(
        base_url=base_url, code=code, mcp_url=mcp_url, mcp_remote_version=version
    )


@router.post("/api/mcp/pairing/complete")
def mcp_pairing_complete(payload: McpPairingComplete):
    from app.services.zomato_mcp_executor import complete_pairing_session, repo_root_from_settings

    try:
        session = None
        try:
            from app.services.zomato_mcp_executor import find_pairing_session_info

            session = find_pairing_session_info(payload.code)
            if session:
                return complete_pairing_session(
                    repo_root=repo_root_from_settings(),
                    code=payload.code,
                    files=payload.files,
                    user_id=session.get("user_id"),
                )
        except Exception:
            pass
        return complete_pairing_session(
            repo_root=repo_root_from_settings(),
            code=payload.code,
            files=payload.files,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e


@router.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
def mcp_oauth_callback(
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    provider: str = Query(default="zomato"),
):
    from app.services.zomato_mcp_executor import complete_web_oauth, repo_root_from_settings

    if error:
        return _oauth_result_page(
            "Authorization failed",
            f"{error}: {error_description or 'The provider did not authorize the request.'}",
            ok=False,
        )
    if not state or not code:
        return _oauth_result_page(
            "Authorization incomplete",
            "The callback did not include both state and code. Start authorization again.",
            ok=False,
        )
    try:
        result = complete_web_oauth(
            repo_root=repo_root_from_settings(), state=state, code=code, provider=provider
        )
        provider_label = result.get("provider_label") or provider.replace("_", " ").title()
        return _oauth_result_page(f"{provider_label} connected", result["detail"], ok=True)
    except Exception as e:
        return _oauth_result_page(
            "Authorization could not finish", f"{type(e).__name__}: {e}", ok=False
        )


@router.get("/api/mcp/oauth/start")
def mcp_oauth_start(
    request: Request,
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    """Return the browser-local mcp-remote pairing command for an MCP provider."""
    from app.services.zomato_mcp_executor import create_pairing_session, repo_root_from_settings

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider or not tool_provider.available:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider}' is not available or not configured.",
        )
    if tool_provider.auth_mode == "url":
        return {
            "status": "not_required",
            "detail": f"{tool_provider.label} authenticates through its MCP URL. Use Test to discover tools.",
            "provider": provider,
            "provider_label": tool_provider.label,
            "mcp_url": mcp_url,
        }
    if tool_provider.auth_mode == "tool" or tool_provider.stdio_command:
        return {
            "status": "not_required",
            "detail": f"{tool_provider.label} does not use Companion pairing. Use Test to verify tools.",
            "provider": provider,
            "provider_label": tool_provider.label,
            "mcp_url": mcp_url or None,
        }

    if not mcp_url.strip():
        return {
            "status": "not_configured",
            "detail": f"No MCP URL configured for {tool_provider.label}. Set the provider URL first.",
            "provider": provider,
            "provider_label": tool_provider.label,
            "mcp_url": None,
        }

    base_url = _public_url(request, "").rstrip("/")
    session = create_pairing_session(
        repo_root=repo_root_from_settings(), mcp_url=mcp_url, user_id=user
    )
    script_url = f"{base_url}/api/mcp/pairing/script?code={quote(session['code'])}"
    return {
        "status": "pairing_ready",
        "detail": (
            f"Run this command on the laptop where your browser can sign into {tool_provider.label}. "
            "It opens the provider through mcp-remote and syncs the session back automatically."
        ),
        "command": f"curl -fsSL '{script_url}' | python3",
        "expires_in_seconds": session["expires_in_seconds"],
        "provider": provider,
        "provider_label": tool_provider.label,
        "mcp_url": mcp_url,
    }


@router.post("/api/mcp/test/")
def mcp_test(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    """Auto-restore tokens from Secrets Manager, then test connection."""
    from app.services.swiggy_mcp_executor import run_swiggy_tool_plan_sync
    from app.services.zomato_mcp_executor import (
        get_mcp_token_status,
        list_mcp_tools_sync,
        repo_root_from_settings,
        restore_mcp_auth_tokens,
        run_tool_plan_sync,
    )

    root = repo_root_from_settings()
    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if tool_provider and not tool_provider.available:
        return {
            "status": "provider_unavailable",
            "detail": tool_provider.notes or "This MCP provider is not configured yet.",
            "mcp_url": None,
        }
    url_authenticated = bool(tool_provider and tool_provider.auth_mode in {"url", "tool"})
    token_status = (
        {
            "has_local_tokens": True,
            "has_secret_tokens": False,
            "auth_mode": tool_provider.auth_mode if tool_provider else "url",
        }
        if url_authenticated
        else get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)
    )

    # Auto-restore from Secrets Manager if needed
    if (
        not url_authenticated
        and not token_status["has_local_tokens"]
        and token_status["has_secret_tokens"]
    ):
        restored = restore_mcp_auth_tokens(root, user_id=user)
        if not restored:
            return {
                "status": "needs_tokens",
                "detail": "Tokens found in Secrets Manager but could not be restored to disk.",
            }
        token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)

    node_binary = secret_or_env("NODE_BINARY", "node")

    if (
        not url_authenticated
        and not token_status["has_local_tokens"]
        and not token_status["has_secret_tokens"]
    ):
        return {
            "status": "needs_tokens",
            "detail": (
                f"No OAuth tokens are available. Use Connect to pair this app with {tool_provider.label if tool_provider else 'the MCP'}; "
                "Companion will store the session in Secrets Manager and restore it on cold starts."
            ),
            "mcp_url": mcp_url,
        }

    # Try connection
    try:
        if tool_provider and tool_provider.stdio_command:
            from app.services.stdio_mcp_executor import list_stdio_mcp_tools_sync

            tools_result = list_stdio_mcp_tools_sync(
                command=tool_provider.stdio_command,
                args=list(tool_provider.stdio_args),
                repo_root=root,
                user_id=user,
            )
            if tools_result.get("ok"):
                return {
                    "status": "connected",
                    "detail": f"Successfully connected to {tool_provider.label}.",
                    "mcp_url": None,
                    "tools": tools_result.get("tools", []),
                }
            return {
                "status": "auth_error",
                "detail": tools_result.get("error_detail", "MCP connection failed."),
                "mcp_url": None,
                "tools": [],
            }
        if tool_provider and (tool_provider.custom or tool_provider.name in {"groww", "kite"}):
            tools_result = list_mcp_tools_sync(
                mcp_url=mcp_url,
                node_binary=node_binary,
                repo_root=root,
                user_id=user,
            )
            if tools_result.get("ok"):
                tool_names = [
                    str(tool.get("name") or "").strip()
                    for tool in tools_result.get("tools", [])
                    if isinstance(tool, dict) and str(tool.get("name") or "").strip()
                ]
                update_tool_provider_tools(tool_provider.name, tool_names, user_id=user)
                return {
                    "status": "connected",
                    "detail": f"Successfully connected to {tool_provider.label}. Discovered {len(tool_names)} tools.",
                    "mcp_url": mcp_url,
                    "tools": tools_result.get("tools", []),
                }
            return {
                "status": "auth_error",
                "detail": tools_result.get(
                    "error_detail", "MCP connection failed — tokens may be expired."
                ),
                "mcp_url": mcp_url,
                "tools": [],
            }

        test_tool = (
            "search_products"
            if provider == "zepto"
            else "search_products"
            if provider == "swiggy_instamart"
            else "search_restaurants"
            if provider == "swiggy_food"
            else "get_saved_addresses_for_user"
        )
        test_args = (
            {"query": "milk"} if test_tool in ("search_products", "search_restaurants") else {}
        )
        if provider in ("swiggy_food", "swiggy_instamart"):
            result = run_swiggy_tool_plan_sync(
                [{"tool": test_tool, "arguments": test_args}],
                mcp_url=mcp_url,
                node_binary=node_binary,
                repo_root=root,
                user_id=user,
            )
        else:
            result = run_tool_plan_sync(
                [{"tool": test_tool, "arguments": test_args}],
                mcp_url=mcp_url,
                node_binary=node_binary,
                repo_root=root,
                user_id=user,
            )
        if result.get("ok"):
            return {
                "status": "connected",
                "detail": f"Successfully connected to {tool_provider.label if tool_provider else 'MCP'}.",
                "mcp_url": mcp_url,
            }
        return {
            "status": "auth_error",
            "detail": result.get("error_detail", "MCP connection failed — tokens may be expired."),
            "mcp_url": mcp_url,
        }
    except Exception as e:
        return {
            "status": "error",
            "detail": f"{type(e).__name__}: {e}",
            "mcp_url": mcp_url,
        }


@router.get("/api/mcp/tokens/status/")
def mcp_tokens_status(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from app.services.zomato_mcp_executor import get_mcp_token_status, repo_root_from_settings

    _, mcp_url = _provider_mcp_url(provider, user)
    token_status = get_mcp_token_status(repo_root_from_settings(), mcp_url=mcp_url, user_id=user)
    token_status["provider"] = provider
    return token_status


@router.get("/api/mcp/tools/")
def mcp_tools(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from app.services.zomato_mcp_executor import list_mcp_tools_sync, repo_root_from_settings

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider:
        raise HTTPException(status_code=404, detail="MCP provider not found.")
    if tool_provider.stdio_command:
        from app.services.stdio_mcp_executor import list_stdio_mcp_tools_sync

        result = list_stdio_mcp_tools_sync(
            repo_root=repo_root_from_settings(),
            command=tool_provider.stdio_command,
            args=list(tool_provider.stdio_args),
            user_id=user,
        )
        result["provider"] = tool_provider.name
        result["provider_label"] = tool_provider.label
        return result
    result = list_mcp_tools_sync(
        repo_root=repo_root_from_settings(),
        mcp_url=mcp_url,
        node_binary=secret_or_env("NODE_BINARY", "node"),
        user_id=user,
    )
    if result.get("ok"):
        update_tool_provider_tools(
            tool_provider.name,
            [
                str(tool.get("name") or "").strip()
                for tool in result.get("tools", [])
                if isinstance(tool, dict) and str(tool.get("name") or "").strip()
            ],
            user_id=user,
        )
    result["provider"] = tool_provider.name
    result["provider_label"] = tool_provider.label
    return result


@router.get("/api/food/saved-addresses/")
def food_saved_addresses(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """List saved delivery addresses for the authenticated user via the selected MCP provider."""
    from app.services.swiggy_mcp_executor import run_swiggy_tool_plan_sync
    from app.services.zomato_mcp_executor import (
        get_mcp_token_status,
        repo_root_from_settings,
        restore_mcp_auth_tokens,
        run_tool_plan_sync,
    )

    root = repo_root_from_settings()
    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider or not tool_provider.available:
        raise HTTPException(
            status_code=400,
            detail=tool_provider.notes if tool_provider else f"Unknown provider {provider!r}.",
        )

    tool_name: str | None = None
    if "get_saved_addresses_for_user" in tool_provider.tools:
        tool_name = "get_saved_addresses_for_user"
    elif "get_addresses" in tool_provider.tools:
        tool_name = "get_addresses"
    elif "get_user_addresses" in tool_provider.tools:
        tool_name = "get_user_addresses"
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Provider {tool_provider.name!r} has no address-listing tool (expected get_saved_addresses_for_user, get_addresses, or get_user_addresses).",
        )

    token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)
    if not token_status["has_local_tokens"] and token_status["has_secret_tokens"]:
        restore_mcp_auth_tokens(root, user_id=user)
        token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)
    if not token_status["has_local_tokens"] and not token_status["has_secret_tokens"]:
        raise HTTPException(
            status_code=503,
            detail="No OAuth tokens for this provider. Connect MCP (pairing or OAuth) first.",
        )

    node_binary = secret_or_env("NODE_BINARY", "node")
    if tool_name in ("get_user_addresses", "get_addresses"):
        result = run_swiggy_tool_plan_sync(
            [{"tool": tool_name, "arguments": {}}],
            mcp_url=mcp_url,
            node_binary=node_binary,
            repo_root=root,
            user_id=user,
        )
    else:
        result = run_tool_plan_sync(
            [{"tool": tool_name, "arguments": {}}],
            mcp_url=mcp_url,
            node_binary=node_binary,
            repo_root=root,
            user_id=user,
        )
    if not result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=str(result.get("error_detail") or "MCP address fetch failed"),
        )
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    rows: list[dict[str, str]] = []
    for step in steps:
        if isinstance(step, dict):
            rows.extend(_extract_saved_addresses_from_mcp_step(step))
    return {
        "status": "success",
        "provider": tool_provider.name,
        "tool": tool_name,
        "addresses": rows,
        "count": len(rows),
    }
