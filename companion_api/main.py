from __future__ import annotations

import os
from html import escape
from datetime import datetime
from typing import Any
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from mangum import Mangum
from pydantic import BaseModel

from companion_api.auth import (
    cognito_enabled,
    create_access_token,
    get_current_user,
    validate_admin_login,
)
from companion_api.secrets import secret_or_env

from app.services.food_llm_router import route_food_query
from app.services.food_response_builder import (
    build_assistant_message_with_llm,
    build_zomato_summary,
)
from app.services.llm_config import resolve_llm_from_env
from app.services.tool_runtime import (
    delete_custom_tool_provider,
    execute_tool_plan_sync,
    get_tool_provider,
    list_tool_providers,
    save_custom_tool_provider,
    select_tool_providers_for_message,
    update_custom_provider_tools,
)
from app.services.zomato_mcp_executor import run_tool_plan_sync
from companion_api import settings
from companion_api.dynamodb import ReminderRepository
from companion_api.models import (
    CustomMcpProviderRequest,
    FoodQueryRequest,
    ReminderCreateRequest,
    ReminderEvent,
    ReminderResponseRequest,
    SendMessageRequest,
)
from companion_api.reminders import ReminderScheduler

app = FastAPI(title="Companion API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "app" / "templates" / "app"


@app.get("/", response_class=HTMLResponse)
@app.get("/food/", response_class=HTMLResponse)
def food_ui():
    """Serve the food-agent SPA."""
    return (_TEMPLATE_DIR / "food_ui.html").read_text()


def _public_url(request: Request, path: str) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}{path}"


def _oauth_result_page(title: str, detail: str, *, ok: bool) -> str:
    safe_title = escape(title)
    safe_detail = escape(detail)
    color = "#047857" if ok else "#b91c1c"
    badge = "Connected" if ok else "Needs attention"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#f6f7f9; color:#111827; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; }}
    main {{ width:min(520px, calc(100% - 32px)); background:white; border:1px solid #e5e7eb; border-radius:12px; padding:28px; box-shadow:0 18px 50px rgba(15,23,42,.08); }}
    .badge {{ display:inline-flex; padding:5px 10px; border-radius:999px; color:{color}; background:#f9fafb; border:1px solid #e5e7eb; font-size:12px; font-weight:800; text-transform:uppercase; }}
    h1 {{ margin:16px 0 8px; font-size:24px; }}
    p {{ color:#4b5563; line-height:1.55; }}
    a {{ display:inline-flex; margin-top:14px; padding:10px 14px; border-radius:8px; background:#2563eb; color:white; text-decoration:none; font-weight:700; }}
  </style>
</head>
<body>
  <main>
    <span class="badge">{badge}</span>
    <h1>{safe_title}</h1>
    <p>{safe_detail}</p>
    <a href="/">Back to Companion</a>
  </main>
</body>
</html>"""


def _pairing_script(*, base_url: str, code: str, mcp_url: str, mcp_remote_version: str) -> str:
    return f'''#!/usr/bin/env python3
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
'''


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str


class McpPairingComplete(BaseModel):
    code: str
    files: dict[str, str]


def _cognito_password_login(username: str, password: str) -> LoginResponse:
    client_id = os.getenv("COGNITO_CLIENT_ID", "").strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cognito client is not configured.",
        )

    client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)
    try:
        response = client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {
            "NotAuthorizedException",
            "UserNotFoundException",
            "PasswordResetRequiredException",
            "UserNotConfirmedException",
        }:
            raise HTTPException(status_code=401, detail="Invalid credentials.") from exc
        raise

    challenge = response.get("ChallengeName")
    if challenge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cognito challenge required: {challenge}.",
        )

    auth = response.get("AuthenticationResult") or {}
    access_token = auth.get("AccessToken")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cognito did not return an access token.",
        )
    return LoginResponse(access_token=access_token, username=username)


@app.post("/api/login/")
def login(payload: LoginRequest) -> LoginResponse:
    if cognito_enabled():
        return _cognito_password_login(payload.username, payload.password)
    if not validate_admin_login(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    token = create_access_token(payload.username)
    return LoginResponse(access_token=token, username=payload.username)


@app.get("/api/auth/status/")
def auth_status(user: str = Depends(get_current_user)):
    return {"authenticated": True, "username": user}


# ── Cognito configuration ────────────────────────────────────────────────


class CognitoConfigOut(BaseModel):
    enabled: bool
    region: str = ""
    user_pool_id: str = ""
    client_id: str = ""
    issuer: str = ""
    domain: str = ""


@app.get("/api/auth/cognito/config/", response_model=CognitoConfigOut)
def cognito_config() -> CognitoConfigOut:
    """Return Cognito configuration for client-side SDK setup."""
    from companion_api.cognito_auth import (
        cognito_client_id,
        cognito_enabled,
        cognito_issuer,
        cognito_user_pool_id,
    )
    if not cognito_enabled():
        return CognitoConfigOut(enabled=False)
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-south-1"))
    domain = os.getenv("COGNITO_DOMAIN", "")
    return CognitoConfigOut(
        enabled=True,
        region=region,
        user_pool_id=cognito_user_pool_id(),
        client_id=cognito_client_id(),
        issuer=cognito_issuer(),
        domain=domain,
    )


def _provider_mcp_url(provider_id: str, user_id: str | None = None) -> tuple[Any, str]:
    tool_provider = get_tool_provider(provider_id, user_id=user_id) or get_tool_provider("zomato", user_id=user_id)
    mcp_url = secret_or_env(
        tool_provider.mcp_url_env if tool_provider and tool_provider.mcp_url_env else "ZOMATO_MCP_URL",
        tool_provider.default_mcp_url if tool_provider and tool_provider.default_mcp_url else "https://mcp-server.zomato.com/mcp",
    )
    return tool_provider, mcp_url


# ---------------------------------------------------------------------------
# MCP status
# ---------------------------------------------------------------------------


@app.get("/api/mcp/status/")
def mcp_status(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from app.services.zomato_mcp_executor import diagnose_mcp_environment, get_mcp_token_status, repo_root_from_settings

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
    url_set = bool(mcp_url.strip())

    root = repo_root_from_settings()
    token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)
    diagnostics = diagnose_mcp_environment(root, mcp_url=mcp_url, node_binary=node_binary, user_id=user)
    has_local = token_status["has_local_tokens"]
    has_secret = token_status["has_secret_tokens"]

    if not url_set:
        status = "not_configured"
        detail = f"{tool_provider.label if tool_provider else 'MCP'} URL is not set."
    elif not diagnostics["node_available"]:
        status = "missing_node"
        detail = f"Node.js is not available for NODE_BINARY={node_binary!r}. Install Node.js or set NODE_BINARY."
    elif not diagnostics["mcp_remote_installed"]:
        status = "missing_bridge"
        detail = "mcp-remote is not installed. Run npm install at the repo root, then refresh status."
    elif not diagnostics["python_mcp_sdk_available"]:
        status = "missing_python_sdk"
        detail = (
            "The Python MCP SDK is not installed for this interpreter. This local shell is likely "
            "using Python 3.9; use Python 3.10+ or the Lambda Python 3.12 runtime, then install requirements."
        )
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


@app.post("/api/mcp/oauth/")
def mcp_initiate_oauth(request: Request, _user: str = Depends(get_current_user)):
    """Start a hosted OAuth flow for Zomato MCP.

    This avoids mcp-remote's localhost browser callback, which is unsuitable for
    Lambda/API Gateway. The callback stores mcp-remote-compatible token files in
    Secrets Manager and /tmp for the current warm Lambda instance.
    """
    from app.services.zomato_mcp_executor import (
        repo_root_from_settings,
        start_web_oauth,
    )

    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    callback_url = _public_url(request, "/api/mcp/oauth/callback")
    try:
        return start_web_oauth(
            repo_root=repo_root_from_settings(),
            mcp_url=mcp_url,
            callback_url=callback_url,
        )
    except Exception as e:
        return {
            "status": "oauth_unavailable",
            "detail": f"{type(e).__name__}: {e}",
            "mcp_url": mcp_url,
        }


@app.post("/api/mcp/pairing/")
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
    base_url = _public_url(request, "").rstrip("/")
    session = create_pairing_session(repo_root=repo_root_from_settings(), mcp_url=mcp_url, user_id=user)
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


@app.get("/api/mcp/pairing/script", response_class=PlainTextResponse)
def mcp_pairing_script(request: Request, code: str = Query(...)):
    base_url = _public_url(request, "").rstrip("/")
    version = "0.1.38"
    try:
        from app.services.zomato_mcp_executor import (
            find_pairing_session_info,
            repo_root_from_settings,
            _mcp_remote_version,
        )

        version = _mcp_remote_version(repo_root_from_settings()) or version
        session = find_pairing_session_info(code)
        if not session:
            return "# Pairing code expired. Generate a new Connect command from Companion.\n"
        mcp_url = str(session.get("mcp_url") or "").strip()
        version = str(session.get("mcp_remote_version") or version)
    except Exception:
        mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    return _pairing_script(base_url=base_url, code=code, mcp_url=mcp_url, mcp_remote_version=version)


@app.post("/api/mcp/pairing/complete")
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


@app.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
def mcp_oauth_callback(
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    from app.services.zomato_mcp_executor import complete_web_oauth, repo_root_from_settings

    if error:
        return _oauth_result_page(
            "Authorization failed",
            f"{error}: {error_description or 'Zomato did not authorize the request.'}",
            ok=False,
        )
    if not state or not code:
        return _oauth_result_page(
            "Authorization incomplete",
            "The callback did not include both state and code. Start authorization again.",
            ok=False,
        )
    try:
        result = complete_web_oauth(repo_root=repo_root_from_settings(), state=state, code=code)
        return _oauth_result_page("Zomato connected", result["detail"], ok=True)
    except Exception as e:
        return _oauth_result_page("Authorization could not finish", f"{type(e).__name__}: {e}", ok=False)


@app.post("/api/mcp/test/")
def mcp_test(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    """Auto-restore tokens from Secrets Manager, then test connection."""
    from app.services.zomato_mcp_executor import (
        get_mcp_token_status,
        list_mcp_tools_sync,
        restore_mcp_auth_tokens,
        repo_root_from_settings,
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
    token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)

    # Auto-restore from Secrets Manager if needed
    if not token_status["has_local_tokens"] and token_status["has_secret_tokens"]:
        restored = restore_mcp_auth_tokens(root, user_id=user)
        if not restored:
            return {
                "status": "needs_tokens",
                "detail": "Tokens found in Secrets Manager but could not be restored to disk.",
            }
        token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)

    node_binary = secret_or_env("NODE_BINARY", "node")

    if not token_status["has_local_tokens"] and not token_status["has_secret_tokens"]:
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
        if tool_provider and tool_provider.custom:
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
                update_custom_provider_tools(tool_provider.name, tool_names, user_id=user)
                return {
                    "status": "connected",
                    "detail": f"Successfully connected to {tool_provider.label}. Discovered {len(tool_names)} tools.",
                    "mcp_url": mcp_url,
                    "tools": tools_result.get("tools", []),
                }
            return {
                "status": "auth_error",
                "detail": tools_result.get("error_detail", "MCP connection failed — tokens may be expired."),
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
        test_args = {"query": "milk"} if test_tool in ("search_products", "search_restaurants") else {}
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


@app.get("/api/mcp/tokens/status/")
def mcp_tokens_status(user: str = Depends(get_current_user)):
    from app.services.zomato_mcp_executor import get_mcp_token_status, repo_root_from_settings
    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    return get_mcp_token_status(repo_root_from_settings(), mcp_url=mcp_url, user_id=user)


@app.get("/api/tools/providers/")
def tool_providers(user: str = Depends(get_current_user)):
    return {"providers": list_tool_providers(user_id=user)}


@app.post("/api/tools/providers/custom/")
def create_custom_tool_provider(
    payload: CustomMcpProviderRequest,
    user: str = Depends(get_current_user),
):
    try:
        provider = save_custom_tool_provider(
            label=payload.label,
            mcp_url=payload.mcp_url,
            tools=payload.tools,
            user_id=user,
        )
        return {"status": "success", "provider": provider}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/tools/providers/custom/{provider_id}")
def remove_custom_tool_provider(
    provider_id: str,
    user: str = Depends(get_current_user),
):
    provider = get_tool_provider(provider_id, user_id=user)
    if not provider or not provider.custom:
        raise HTTPException(status_code=404, detail="Custom MCP provider not found.")
    deleted = delete_custom_tool_provider(provider_id, user_id=user)
    return {"status": "success" if deleted else "missing", "provider_id": provider_id}


@app.get("/api/mcp/tools/")
def mcp_tools(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from app.services.zomato_mcp_executor import list_mcp_tools_sync, repo_root_from_settings

    tool_provider, mcp_url = _provider_mcp_url(provider, user)
    if not tool_provider:
        raise HTTPException(status_code=404, detail="MCP provider not found.")
    result = list_mcp_tools_sync(
        repo_root=repo_root_from_settings(),
        mcp_url=mcp_url,
        node_binary=secret_or_env("NODE_BINARY", "node"),
        user_id=user,
    )
    if result.get("ok") and tool_provider.custom:
        update_custom_provider_tools(
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


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.post("/api/send-message/")
def send_whatsapp_message(
    payload: SendMessageRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    message = payload.message.strip()
    whatsapp_url = (
        f"https://wa.me/{settings.COMPANION_WHATSAPP_CC_NUMBER}?text={quote(message)}"
    )

    llm_cfg, err_detail = resolve_llm_from_env(secret_or_env("FOOD_ROUTER_PROVIDER"))
    if err_detail:
        llm_payload: dict[str, Any] = {
            "status": "skipped" if payload.llm_optional else "error",
            "reason": err_detail,
        }
    elif not llm_cfg:
        llm_payload = {
            "status": "skipped" if payload.llm_optional else "error",
            "reason": "Could not select an LLM provider.",
        }
    else:
        try:
            llm_payload = {
                "status": "success",
                "provider": llm_cfg.provider,
                "food_routing_plan": route_food_query(
                    user_message=message,
                    api_key=llm_cfg.api_key,
                    model=llm_cfg.model,
                    provider=llm_cfg.provider,
                    base_url=llm_cfg.base_url,
                ),
            }
        except ValueError as e:
            llm_payload = {"status": "error", "reason": str(e)}
        except Exception as e:
            llm_payload = {"status": "error", "reason": f"{type(e).__name__}: {e}"}

    if llm_payload["status"] != "success" and not payload.llm_optional:
        status = "partial"
    else:
        status = "success"

    response: dict[str, Any] = {"status": status, "llm": llm_payload}
    if payload.include_whatsapp_link:
        response["whatsapp_url"] = whatsapp_url
    return response


@app.post("/api/food/query/")
def food_agent_full(
    payload: FoodQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    provider_override = (payload.provider or "").strip()
    llm_cfg, err = resolve_llm_from_env(
        provider_override or secret_or_env("FOOD_ROUTER_PROVIDER")
    )
    if err or not llm_cfg:
        raise HTTPException(status_code=503, detail=err or "LLM not configured.")

    history = payload.history if isinstance(payload.history, list) else []
    recent_history = [
        item
        for item in history[-8:]
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and item.get("content")
    ]
    routing_message = payload.message.strip()
    if recent_history:
        transcript = "\n".join(
            f"{item.get('role')}: {str(item.get('content') or '')[:700]}"
            for item in recent_history
        )
        routing_message = f"Conversation so far:\n{transcript}\n\nCurrent user message:\n{payload.message.strip()}"

    explicit_providers = {
        provider.strip()
        for provider in (payload.tool_providers or [])
        if provider and provider.strip()
    }
    if payload.tool_provider and payload.tool_provider.strip():
        explicit_providers = {payload.tool_provider.strip()}
    routed_provider_ids = explicit_providers or select_tool_providers_for_message(payload.message, user_id=user)
    primary_provider_id = next(iter(sorted(routed_provider_ids))) if routed_provider_ids else None

    try:
        selected_tool_provider = get_tool_provider(primary_provider_id or "", user_id=user)
        if selected_tool_provider and selected_tool_provider.custom and not selected_tool_provider.tools:
            raise HTTPException(
                status_code=409,
                detail="Test this custom MCP first so Companion can discover its tools.",
            )
        plan = route_food_query(
            user_message=routing_message,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
            provider=llm_cfg.provider,
            base_url=llm_cfg.base_url,
            tool_provider=primary_provider_id,
            tool_providers=routed_provider_ids or None,
            tool_provider_label=selected_tool_provider.label if selected_tool_provider else None,
            tool_provider_tools=(
                sorted(selected_tool_provider.tools)
                if selected_tool_provider and selected_tool_provider.custom and selected_tool_provider.tools
                else None
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e

    execute_tools = (
        payload.execute_tools
        if payload.execute_tools is not None
        else (
            payload.execute_zomato
            if payload.execute_zomato is not None
            else settings.EXECUTE_TOOLS_BY_DEFAULT
        )
    )
    zomato_raw: dict[str, Any] | None = None
    tool_execution: dict[str, Any] | None = None
    if execute_tools:
        tool_plan = plan.get("tool_plan") if isinstance(plan, dict) else []
        enabled_providers = routed_provider_ids or None
        tool_execution = execute_tool_plan_sync(
            tool_plan if isinstance(tool_plan, list) else [],
            address_id=payload.address_id,
            user_message=payload.message,
            constraints=plan.get("constraints") if isinstance(plan, dict) else {},
            enabled_providers=enabled_providers,
            user_id=user,
        )
        provider_results = tool_execution.get("provider_results")
        provider_summaries: dict[str, Any] = {}
        if isinstance(provider_results, dict):
            for provider_name, raw in provider_results.items():
                if isinstance(raw, dict):
                    provider_summaries[provider_name] = build_zomato_summary(raw)
            zraw = provider_results.get("zomato")
            if isinstance(zraw, dict):
                zomato_raw = zraw
        zomato_summary = build_zomato_summary(zomato_raw)
    else:
        provider_summaries = {}
        zomato_summary = build_zomato_summary(None)

    out: dict[str, Any] = {
        "status": "success",
        "provider": llm_cfg.provider,
        "selected_tool_providers": sorted(routed_provider_ids),
        "food_routing_plan": plan,
        "tool_execution": {
            "requested": bool(execute_tools),
            "routed_providers": (
                tool_execution.get("routed_providers", [])
                if isinstance(tool_execution, dict)
                else []
            ),
            "ok": tool_execution.get("ok") if isinstance(tool_execution, dict) else False,
            "error": tool_execution.get("error") if isinstance(tool_execution, dict) else None,
        },
        "zomato_summary": zomato_summary,
        "provider_summaries": provider_summaries,
        "assistant_message": build_assistant_message_with_llm(
            user_message=payload.message,
            plan=plan,
            zomato_summary=zomato_summary,
            provider=llm_cfg.provider,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
            base_url=llm_cfg.base_url,
        ),
    }
    if payload.include_raw_zomato:
        out["zomato_raw"] = zomato_raw
    return out


@app.post("/api/reminders/")
def create_reminder(
    payload: ReminderCreateRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    scheduler = ReminderScheduler()
    reminder = scheduler.create_reminder(
        {
            **payload.model_dump(),
            "member_id": payload.member_id,
            "next_trigger": payload.next_trigger.isoformat(),
        }
    )
    return {"status": "success", "reminder": reminder}


@app.get("/api/reminders/pending/")
def pending_reminders(
    member_id: str = Query(min_length=1),
    _user: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    reminders = ReminderRepository().list_pending(member_id)
    return [
        {
            "id": r["reminder_id"],
            "reminder_id": r["reminder_id"],
            "member_id": r["member_id"],
            "message": r.get("title", ""),
            "description": r.get("description", ""),
            "next_trigger": r.get("next_trigger"),
        }
        for r in reminders
    ]


@app.post("/api/reminders/respond/")
def respond_reminder(
    payload: ReminderResponseRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        reminder = ReminderScheduler().respond(
            member_id=payload.member_id,
            reminder_id=payload.reminder_id,
            response=payload.response,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"status": "success", "reminder_id": reminder["reminder_id"]}


def scheduled_reminder_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    payload = ReminderEvent.model_validate(event)
    reminder = ReminderScheduler().mark_triggered(
        payload.member_id,
        payload.reminder_id,
    )
    return {
        "status": "ok",
        "triggered": bool(reminder),
        "triggered_at": datetime.utcnow().isoformat() + "Z",
    }


handler = Mangum(app, lifespan="off")
