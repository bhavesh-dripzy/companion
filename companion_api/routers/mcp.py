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

from companion_api.auth import get_current_user
from companion_api.mcp_address_utils import saved_address_label_from_mcp_item
from companion_api.secrets import secret_or_env
from companion_api.tool_runtime import (
    get_tool_provider,
    list_tool_providers,
    update_tool_provider_tools,
)
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


class McpOauthComplete(BaseModel):
    text: str


def _paste_forced(provider: str) -> bool:
    """Providers pinned to paste completion via MCP_OAUTH_PASTE_PROVIDERS (comma list)."""
    raw = secret_or_env("MCP_OAUTH_PASTE_PROVIDERS", "")
    return provider in {p.strip() for p in raw.split(",") if p.strip()}


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


def _extract_saved_addresses_from_envelope(envelope: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize saved-address rows from a tool-result envelope (Zomato, Swiggy, etc.)."""
    rows: list[dict[str, str]] = []
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        return rows
    payload = envelope.get("data")
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
    from core.providers.mcp.diagnostics import diagnose_mcp_environment
    from core.providers.mcp.remote import repo_root
    from core.providers.mcp.token_store import get_mcp_token_status, restore_mcp_auth_tokens

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
            repo_root() / tool_provider.stdio_args[0] if tool_provider.stdio_args else None
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
    if provider == "kite":
        from core.providers.plugins.kite.transport import probe_profile, vaulted_sid

        sid = vaulted_sid(user)
        probe = probe_profile(sid) if sid else {"authed": False, "session_alive": False}
        authed = bool(probe.get("authed"))
        if authed:
            who = probe.get("profile")
            detail = f"Connected to Zerodha{f' as {who}' if who else ''}."
        elif sid and probe.get("session_alive"):
            detail = "Login started but not finished — tap Connect and complete the Zerodha login."
        else:
            detail = (
                "Not connected. Tap Connect to log in to Zerodha (session lasts the trading day)."
            )
        return {
            "provider": tool_provider.name,
            "provider_label": tool_provider.label,
            "status": "ready" if authed else "needs_login",
            "detail": detail,
            "mcp_url": mcp_url or None,
            "node_binary": node_binary,
            "has_local_tokens": authed,
            "has_secret_tokens": authed,
            "token_status": {"auth_mode": "tool", "transport": "http", "session": bool(sid)},
            "diagnostics": {
                "transport": "streamable-http",
                "session_alive": probe.get("session_alive"),
            },
        }
    url_set = bool(mcp_url.strip())

    root = repo_root()
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
    from core.providers.mcp.remote import config_dir, repo_root
    from core.providers.mcp.token_store import (
        mcp_token_secret,
        merge_mcp_token_secret,
        server_url_hash,
    )

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider:
        raise HTTPException(status_code=404, detail=f"Unknown MCP provider {provider!r}.")
    if provider == "kite":
        from core.providers.plugins.kite.transport import clear_sid

        clear_sid(user)
        return {
            "status": "disconnected",
            "provider": provider,
            "detail": "Zerodha session forgotten. Connect again any time.",
        }
    if not mcp_url:
        raise HTTPException(
            status_code=400,
            detail=f"{tool_provider.label} has no MCP URL configured; nothing to disconnect.",
        )

    url_hash = server_url_hash(mcp_url)
    root = repo_root()

    # 1. Strip the provider's files from the durable Secrets Manager copy.
    #    mcp-remote names files `<md5(mcp_url)>_<kind>` so filtering by the
    #    hash leaves every other provider's tokens untouched.
    secret = mcp_token_secret(user)
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
            merge_mcp_token_secret(user, files=kept)

    # 2. Delete the same files from the live Lambda /tmp (or local dev dir).
    cfg_dir = config_dir(user, root)
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
    from core.providers.mcp.oauth import start_web_oauth

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
            mcp_url=mcp_url,
            callback_url=callback_url,
            user_id=user,
            provider_label=tool_provider.label,
            provider_id=provider,
            force_paste=_paste_forced(provider),
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
    from core.providers.mcp.pairing import create_pairing_session

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
    session = create_pairing_session(mcp_url=mcp_url, user_id=user)
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
        from core.providers.mcp.pairing import find_pairing_session_info
        from core.providers.mcp.remote import remote_version, repo_root

        version = remote_version(repo_root()) or version
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
    from core.providers.mcp.pairing import complete_pairing_session, find_pairing_session_info

    try:
        session = None
        try:
            session = find_pairing_session_info(payload.code)
            if session:
                return complete_pairing_session(
                    code=payload.code,
                    files=payload.files,
                    user_id=session.get("user_id"),
                )
        except Exception:
            pass
        return complete_pairing_session(
            code=payload.code,
            files=payload.files,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e


@router.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
def mcp_oauth_callback(
    request: Request,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    provider: str = Query(default="zomato"),
):
    from core.providers.mcp.oauth_complete import complete_web_oauth

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
        result = complete_web_oauth(state=state, code=code, provider=provider)
        provider_label = result.get("provider_label") or provider.replace("_", " ").title()
        provider_id = str(result.get("provider_id") or provider)
        return _oauth_result_page(
            f"{provider_label} connected",
            result["detail"],
            ok=True,
            redirect_to=_public_url(request, f"/?mcp_oauth=success&provider={quote(provider_id)}"),
        )
    except Exception as e:
        return _oauth_result_page(
            "Authorization could not finish", f"{type(e).__name__}: {e}", ok=False
        )


@router.post("/api/mcp/oauth/complete/")
def mcp_oauth_complete_paste(
    payload: McpOauthComplete,
    user: str = Depends(get_current_user),
):
    """Finish a paste-back OAuth flow from the post-login URL the user pasted."""
    from core.providers.mcp.oauth_complete import complete_web_oauth_from_text

    try:
        return complete_web_oauth_from_text(text=payload.text, user_id=user)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}") from e


@router.get("/api/mcp/oauth/start")
def mcp_oauth_start(
    request: Request,
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    """Start the phone-first connect flow: hosted OAuth (redirect or paste-back
    completion), falling back to the laptop pairing command only if OAuth
    cannot be negotiated at all."""
    from core.providers.mcp.oauth import start_web_oauth
    from core.providers.mcp.pairing import create_pairing_session

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
        if provider == "kite":
            # Kite: mint a fresh MCP session, vault its id, and hand back the
            # Zerodha login URL. Auth binds to the session id server-side, so
            # once the user logs in every later turn re-attaches authed.
            from core.providers.plugins.kite.transport import save_sid, start_login

            try:
                started = start_login()
                save_sid(user, started["sid"])
                return {
                    "status": "oauth_required",
                    "completion": "external",
                    "authorization_url": started["authorization_url"],
                    "detail": (
                        "Log in to Zerodha in the new tab (link valid ~5 min). "
                        "Once you finish, this status flips to Connected — tap refresh to check."
                    ),
                    "provider": provider,
                    "provider_label": tool_provider.label,
                    "mcp_url": mcp_url or None,
                }
            except Exception as e:
                logger.exception("kite login mint failed")
                return {
                    "status": "oauth_unavailable",
                    "detail": f"{type(e).__name__}: {e}",
                    "provider": provider,
                    "provider_label": tool_provider.label,
                    "mcp_url": mcp_url or None,
                }
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

    callback_url = _public_url(request, "/api/mcp/oauth/callback")
    try:
        result = start_web_oauth(
            mcp_url=mcp_url,
            callback_url=callback_url,
            user_id=user,
            provider_label=tool_provider.label,
            provider_id=provider,
            force_paste=_paste_forced(provider),
        )
        result["provider"] = provider
        result["provider_label"] = tool_provider.label
        return result
    except Exception:
        logger.exception("Hosted OAuth start failed for %s; falling back to pairing", provider)

    base_url = _public_url(request, "").rstrip("/")
    session = create_pairing_session(mcp_url=mcp_url, user_id=user)
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
    from companion_api.mcp_probe import list_remote_tools, list_stdio_tools, run_remote_tool
    from core.providers.mcp.remote import repo_root
    from core.providers.mcp.token_store import get_mcp_token_status, restore_mcp_auth_tokens

    root = repo_root()
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
            tools_result = list_stdio_tools(
                tool_provider.stdio_command,
                list(tool_provider.stdio_args),
                user_id=user,
                root=root,
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
            tools_result = list_remote_tools(mcp_url, user_id=user, root=root)
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
        result = run_remote_tool(mcp_url, test_tool, test_args, user_id=user, root=root)
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
    from core.providers.mcp.remote import repo_root
    from core.providers.mcp.token_store import get_mcp_token_status

    _, mcp_url = _provider_mcp_url(provider, user)
    token_status = get_mcp_token_status(repo_root(), mcp_url=mcp_url, user_id=user)
    token_status["provider"] = provider
    return token_status


@router.get("/api/mcp/tools/")
def mcp_tools(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from companion_api.mcp_probe import list_remote_tools, list_stdio_tools

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider:
        raise HTTPException(status_code=404, detail="MCP provider not found.")
    if tool_provider.stdio_command:
        result = list_stdio_tools(
            tool_provider.stdio_command,
            list(tool_provider.stdio_args),
            user_id=user,
        )
        result["provider"] = tool_provider.name
        result["provider_label"] = tool_provider.label
        return result
    result = list_remote_tools(mcp_url, user_id=user)
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
    from companion_api.mcp_probe import run_remote_tool
    from core.providers.mcp.remote import repo_root
    from core.providers.mcp.token_store import get_mcp_token_status, restore_mcp_auth_tokens

    root = repo_root()
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
    elif "list_saved_addresses" in tool_provider.tools:
        tool_name = "list_saved_addresses"  # Zepto (prose reply, parsed below)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Provider {tool_provider.name!r} has no address-listing tool (expected get_saved_addresses_for_user, get_addresses, get_user_addresses, or list_saved_addresses).",
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

    result = run_remote_tool(mcp_url, tool_name, {}, user_id=user, root=root)
    if not result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=str(result.get("error_detail") or "MCP address fetch failed"),
        )
    if tool_name == "list_saved_addresses":
        # Zepto returns prose with an id footer — parse it to {address_id, location_name}.
        from core.providers.plugins.zepto.address import extract_zepto_address_rows

        parsed = extract_zepto_address_rows(result.get("data") or {})
        rows = [
            {
                "address_id": r["address_id"],
                "location_name": (
                    f"{r['tag']} — {r['line']}" if r.get("tag") and r.get("line")
                    else (r.get("line") or r.get("tag") or r["address_id"])
                ),
            }
            for r in parsed
        ]
    else:
        rows = _extract_saved_addresses_from_envelope(result.get("data"))
    return {
        "status": "success",
        "provider": tool_provider.name,
        "tool": tool_name,
        "addresses": rows,
        "count": len(rows),
    }


# ── Instamart default delivery address (per-user source of truth) ──────────────
# Stored per-user in DynamoDB (ProfileCacheRepository / PROFILE_CACHE_TABLE) —
# the right home for a non-secret, every-turn-read preference (fast reads, no
# Secrets Manager SCP/throttle limits). This is the single source of truth the
# cart flow pins to; auth is untouched.

from companion_api.instamart_address import (  # noqa: E402
    get_default_instamart_address,
    save_default_instamart_address,
)


class InstamartDefaultAddress(BaseModel):
    address_id: str
    tag: str | None = None
    line: str | None = None


@router.get("/api/mcp/instamart/default-address/")
def get_instamart_default_address(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Return the user's saved Instamart default delivery address (or null)."""
    return {"default_address": get_default_instamart_address(user)}


@router.post("/api/mcp/instamart/default-address/")
def set_instamart_default_address(
    payload: InstamartDefaultAddress,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist the user's chosen Instamart default delivery address.

    Validates serviceability first (the only reliable signal is update_cart),
    keeping the cart safe — a non-serviceable address is NOT saved and the
    existing cart is left untouched. Returns ``serviceable`` (+ any cart items
    that didn't survive a switch). Fails OPEN if the check itself errors, so a
    transient MCP hiccup never blocks onboarding."""
    address_id = payload.address_id.strip()
    if not address_id:
        raise HTTPException(status_code=400, detail="address_id is required.")

    serviceable, switched, dropped, message = True, False, [], ""
    try:
        from companion_api.instamart_serviceability import check_serviceability_and_switch

        verdict = check_serviceability_and_switch(user, address_id)
        serviceable = bool(verdict.get("serviceable", True))
        switched = bool(verdict.get("switched"))
        dropped = verdict.get("dropped") or []
        message = str(verdict.get("message") or "")
    except Exception:  # pragma: no cover - never block a save on a probe hiccup
        logger.exception("serviceability check failed for %s; saving without it", address_id)

    if not serviceable:
        return {
            "status": "not_serviceable",
            "serviceable": False,
            "message": message or "Instamart doesn't deliver to this address. Please pick another.",
            "default_address": get_default_instamart_address(user),
        }

    record = save_default_instamart_address(
        user, address_id=address_id, tag=payload.tag, line=payload.line
    )
    return {
        "status": "saved",
        "serviceable": True,
        "switched": switched,
        "dropped_items": dropped,
        "default_address": record,
    }


# ── Swiggy Food default delivery ADDRESS (per-user source of truth) ────────────
# Mirrors Instamart: Swiggy Food takes a saved-address id (addressId) on search /
# menu / cart / order, so the source of truth is a saved-address id captured once
# during onboarding. Serviceability on Swiggy is per-restaurant (not per-address),
# so there's nothing to validate at save time — just persist it.

from companion_api.swiggy_food_address import (  # noqa: E402
    get_default_swiggy_food_address,
    save_default_swiggy_food_address,
)


class SwiggyFoodDefaultAddress(BaseModel):
    address_id: str
    tag: str | None = None
    line: str | None = None


@router.get("/api/mcp/swiggy-food/default-address/")
def get_swiggy_food_default_address(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Return the user's saved Swiggy Food default delivery address (or null)."""
    return {"default_address": get_default_swiggy_food_address(user)}


@router.post("/api/mcp/swiggy-food/default-address/")
def set_swiggy_food_default_address(
    payload: SwiggyFoodDefaultAddress,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist the user's chosen Swiggy Food default delivery address."""
    address_id = payload.address_id.strip()
    if not address_id:
        raise HTTPException(status_code=400, detail="address_id is required.")
    record = save_default_swiggy_food_address(
        user, address_id=address_id, tag=payload.tag, line=payload.line
    )
    return {"status": "saved", "default_address": record}


# ── Zepto default delivery location (per-user source of truth) ─────────────────
# Zepto binds the cart to a *store*, resolved from a GPS coordinate — so unlike
# Instamart (a saved-address id), Zepto's source of truth is a {lat,lng} point
# captured once during onboarding. Same storage strategy (DynamoDB + cache).

from companion_api.zepto_location import (  # noqa: E402
    get_default_zepto_location,
    save_default_zepto_location,
)


class ZeptoDefaultLocation(BaseModel):
    latitude: float
    longitude: float
    label: str | None = None
    address_id: str | None = None


@router.get("/api/mcp/zepto/default-location/")
def get_zepto_default_location(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Return the user's saved Zepto default delivery location (or null)."""
    return {"default_location": get_default_zepto_location(user)}


@router.post("/api/mcp/zepto/default-location/")
def set_zepto_default_location(
    payload: ZeptoDefaultLocation,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist the user's chosen Zepto default delivery location."""
    try:
        record = save_default_zepto_location(
            user,
            latitude=payload.latitude,
            longitude=payload.longitude,
            label=payload.label,
            address_id=payload.address_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "default_location": record}


# ── Zepto default delivery ADDRESS (per-user source of truth) ──────────────────
# Mirrors Instamart: Zepto's select_saved_address binds BOTH the store and the
# order's delivery doorstep from one saved-address id, so the source of truth is
# a saved-address id (not a raw GPS point). Validates serviceability first.

from companion_api.zepto_address import (  # noqa: E402
    get_default_zepto_address,
    save_default_zepto_address,
)


class ZeptoDefaultAddress(BaseModel):
    address_id: str
    tag: str | None = None
    line: str | None = None


@router.get("/api/mcp/zepto/default-address/")
def get_zepto_default_address(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Return the user's saved Zepto default delivery address (or null)."""
    return {"default_address": get_default_zepto_address(user)}


@router.post("/api/mcp/zepto/default-address/")
def set_zepto_default_address(
    payload: ZeptoDefaultAddress,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist the user's chosen Zepto default delivery address.

    Validates serviceability first via select_saved_address (which binds the
    store) — a non-serviceable address is NOT saved. Fails OPEN on a probe
    hiccup so a transient MCP error never blocks onboarding."""
    address_id = payload.address_id.strip()
    if not address_id:
        raise HTTPException(status_code=400, detail="address_id is required.")

    serviceable, message = True, ""
    try:
        from companion_api.zepto_serviceability import check_serviceability_and_switch

        verdict = check_serviceability_and_switch(user, address_id)
        serviceable = bool(verdict.get("serviceable", True))
        message = str(verdict.get("message") or "")
    except Exception:  # pragma: no cover - never block a save on a probe hiccup
        logger.exception("zepto serviceability check failed for %s; saving anyway", address_id)

    if not serviceable:
        return {
            "status": "not_serviceable",
            "serviceable": False,
            "message": message or "Zepto doesn't deliver to this address. Please pick another.",
            "default_address": get_default_zepto_address(user),
        }

    record = save_default_zepto_address(
        user, address_id=address_id, tag=payload.tag, line=payload.line
    )
    return {"status": "saved", "serviceable": True, "default_address": record}


# ── District default location (per-user source of truth) ──────────────────────
# District search tools (movies / events / dining / theatres) take a numeric
# {lat,lng} location and have no saved-address concept. So District's source of
# truth is a {lat,lng} point captured once during onboarding — same storage
# strategy as Zepto's default location (DynamoDB + local-file fallback + cache).

from companion_api.district_location import (  # noqa: E402
    get_default_district_location,
    save_default_district_location,
)


class DistrictDefaultLocation(BaseModel):
    latitude: float
    longitude: float
    label: str | None = None


@router.get("/api/mcp/district/default-location/")
def get_district_default_location(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """Return the user's saved District default location (or null)."""
    return {"default_location": get_default_district_location(user)}


@router.post("/api/mcp/district/default-location/")
def set_district_default_location(
    payload: DistrictDefaultLocation,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist the user's chosen District default location."""
    try:
        record = save_default_district_location(
            user,
            latitude=payload.latitude,
            longitude=payload.longitude,
            label=payload.label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "default_location": record}
