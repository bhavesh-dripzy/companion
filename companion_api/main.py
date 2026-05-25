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
from fastapi.staticfiles import StaticFiles
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
from app.services.food_response_builder import saved_address_label_from_mcp_item
from app.services.agents.pipeline import CustomMcpNeedsDiscoveryError, run_agent_turn
from app.services.calendar import execute_reminder_calendar
from app.services.llm_config import resolve_llm_from_env
from app.services.tool_runtime import (
    delete_custom_tool_provider,
    get_tool_provider,
    list_tool_providers,
    save_custom_tool_provider,
    update_tool_provider_tools,
)
from companion_api import settings
from companion_api.dynamodb import ReminderRepository
from companion_api.models import (
    AgentQueryRequest,
    CustomMcpProviderRequest,
    FoodQueryRequest,
    GoogleCalendarReminderRequest,
    MapsRouteRequest,
    ReminderCreateRequest,
    ReminderEvent,
    ReminderResponseRequest,
    SendMessageRequest,
    ZapierMcpProviderRequest,
)
from companion_api.reminders import ReminderScheduler
from companion_api.google_calendar import (
    complete_google_calendar_oauth,
    google_calendar_status,
    start_google_calendar_oauth,
)
from companion_api.google_maps import (
    GoogleMapsError,
    GeoPoint,
    compute_route,
    google_maps_configured,
)
from app.services.whatsapp.handler import handle_whatsapp_webhook_event

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


_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/", response_class=HTMLResponse)
@app.get("/food/", response_class=HTMLResponse)
def food_ui():
    """Serve the new React frontend SPA."""
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.exists():
        # Fallback to legacy UI if frontend isn't built
        legacy_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "app" / "food_ui.html"
        if legacy_path.exists():
            return legacy_path.read_text()
        return HTMLResponse("Frontend not found. Run 'npm run build' in the frontend directory.", status_code=404)
    return index_path.read_text()


@app.get("/favicon.svg")
def favicon():
    fav_path = _FRONTEND_DIST / "favicon.svg"
    if fav_path.exists():
        return HTMLResponse(fav_path.read_text(), media_type="image/svg+xml")
    return HTMLResponse("", status_code=404)


# Mount static assets (JS, CSS, images) from the React build
if (_FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")
# Mount public folder files (favicon, etc.)
if _FRONTEND_DIST.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIST)), name="static")


def _public_url(request: Request, path: str) -> str:
    configured_base_url = secret_or_env("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured_base_url:
        return f"{configured_base_url}{path}"

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


# ---------------------------------------------------------------------------
# WhatsApp Webhook
# ---------------------------------------------------------------------------


@app.get("/api/whatsapp/webhook")
def whatsapp_webhook_verify(
    request: Request,
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """Verify the WhatsApp webhook with Meta."""
    expected_token = secret_or_env("WHATSAPP_VERIFY_TOKEN", settings.WHATSAPP_VERIFY_TOKEN)
    if mode == "subscribe" and token == expected_token:
        logger.info("WhatsApp webhook verified successfully.")
        return HTMLResponse(content=challenge, status_code=200)
    logger.warning("WhatsApp webhook verification failed. mode=%s, token=%s", mode, token)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook_post(request: Request):
    """Receive incoming WhatsApp messages and events."""
    payload = await request.json()
    # Execute background processing to return 200 OK to Meta quickly (within 3s)
    # Since this is a Lambda, we might want to use a separate task or just process if it's fast.
    # For now, we process it directly.
    try:
        handle_whatsapp_webhook_event(payload)
    except Exception:
        logger.exception("Error handling WhatsApp webhook")
    return {"status": "ok"}


def _provider_mcp_url(provider_id: str, user_id: str | None = None) -> tuple[Any, str]:
    tool_provider = get_tool_provider(provider_id, user_id=user_id) or get_tool_provider("zomato", user_id=user_id)
    if tool_provider and tool_provider.stdio_command:
        return tool_provider, ""
    mcp_url = secret_or_env(
        tool_provider.mcp_url_env if tool_provider and tool_provider.mcp_url_env else "ZOMATO_MCP_URL",
        tool_provider.default_mcp_url if tool_provider and tool_provider.default_mcp_url else "https://mcp-server.zomato.com/mcp",
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
    diagnostics = diagnose_mcp_environment(root, mcp_url=mcp_url, node_binary=node_binary, user_id=user)
    has_local = token_status["has_local_tokens"]
    has_secret = token_status["has_secret_tokens"]
    url_authenticated = bool(tool_provider and tool_provider.auth_mode == "url")

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


@app.post("/api/mcp/oauth/")
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
        raise HTTPException(status_code=400, detail=f"Provider '{provider}' is not available or not configured.")
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
    if tool_provider and (tool_provider.auth_mode == "tool" or tool_provider.stdio_command):
        return {
            "status": "not_required",
            "detail": f"{tool_provider.label} does not use Companion pairing. Use Test to verify tools.",
            "mcp_url": mcp_url or None,
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
        return _oauth_result_page("Authorization could not finish", f"{type(e).__name__}: {e}", ok=False)


@app.get("/api/mcp/oauth/start")
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
    session = create_pairing_session(repo_root=repo_root_from_settings(), mcp_url=mcp_url, user_id=user)
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


@app.post("/api/mcp/test/")
def mcp_test(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    """Auto-restore tokens from Secrets Manager, then test connection."""
    from app.services.swiggy_mcp_executor import run_swiggy_tool_plan_sync
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
    if not url_authenticated and not token_status["has_local_tokens"] and token_status["has_secret_tokens"]:
        restored = restore_mcp_auth_tokens(root, user_id=user)
        if not restored:
            return {
                "status": "needs_tokens",
                "detail": "Tokens found in Secrets Manager but could not be restored to disk.",
            }
        token_status = get_mcp_token_status(root, mcp_url=mcp_url, user_id=user)

    node_binary = secret_or_env("NODE_BINARY", "node")

    if not url_authenticated and not token_status["has_local_tokens"] and not token_status["has_secret_tokens"]:
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


@app.get("/api/mcp/tokens/status/")
def mcp_tokens_status(
    provider: str = Query(default="zomato"),
    user: str = Depends(get_current_user),
):
    from app.services.zomato_mcp_executor import get_mcp_token_status, repo_root_from_settings
    _, mcp_url = _provider_mcp_url(provider, user)
    token_status = get_mcp_token_status(repo_root_from_settings(), mcp_url=mcp_url, user_id=user)
    token_status["provider"] = provider
    return token_status


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


@app.post("/api/tools/providers/zapier/")
def save_zapier_tool_provider(
    payload: ZapierMcpProviderRequest,
    user: str = Depends(get_current_user),
):
    try:
        provider = save_custom_tool_provider(
            label="Zapier MCP",
            mcp_url=payload.mcp_url,
            tools=payload.tools,
            auth_mode="url",
            provider_id="zapier_mcp",
            user_id=user,
        )
        return {"status": "success", "provider": provider}
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


@app.get("/api/google-calendar/status/")
def google_calendar_connection_status(user: str = Depends(get_current_user)) -> dict[str, Any]:
    return google_calendar_status(user)


@app.get("/api/google-calendar/oauth/start")
def google_calendar_oauth_start(
    request: Request,
    user: str = Depends(get_current_user),
) -> dict[str, str]:
    try:
        return start_google_calendar_oauth(
            user_id=user,
            redirect_uri=_public_url(request, "/api/google-calendar/oauth/callback"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/google-calendar/oauth/callback", response_class=HTMLResponse)
def google_calendar_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
):
    if not code or not state:
        return _oauth_result_page(
            "Google Calendar connection failed",
            "Google did not return the expected authorization code.",
            ok=False,
        )
    try:
        complete_google_calendar_oauth(code=code, state=state)
        return _oauth_result_page(
            "Google Calendar connected",
            "Companion can now create calendar events with email reminders.",
            ok=True,
        )
    except Exception as exc:
        return _oauth_result_page(
            "Google Calendar connection failed",
            f"{type(exc).__name__}: {exc}",
            ok=False,
        )


# ---------------------------------------------------------------------------
# Google Maps (Routes API) — distance + ETA from current location
# ---------------------------------------------------------------------------


@app.get("/api/maps/status/")
def maps_status(_user: str = Depends(get_current_user)) -> dict[str, bool]:
    """Used by the frontend to decide whether to surface map-aware UI."""
    return {"configured": google_maps_configured()}


@app.post("/api/maps/route")
def maps_route(
    payload: MapsRouteRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Compute distance + ETA from the user's current location to a destination.

    Either ``destination_text`` *or* ``destination_point`` must be supplied.
    """
    if not payload.destination_text and not payload.destination_point:
        raise HTTPException(
            status_code=422,
            detail="Provide either destination_text or destination_point.",
        )

    destination: Any = (
        GeoPoint(
            latitude=payload.destination_point.latitude,
            longitude=payload.destination_point.longitude,
        )
        if payload.destination_point is not None
        else payload.destination_text
    )

    try:
        route = compute_route(
            origin=GeoPoint(
                latitude=payload.origin.latitude,
                longitude=payload.origin.longitude,
            ),
            destination=destination,
            travel_mode=payload.travel_mode,
            language=payload.language,
            region=payload.region,
        )
    except GoogleMapsError as exc:
        status = (
            503
            if exc.code == "not_configured"
            else 422
            if exc.code in {"invalid_input", "no_route"}
            else 502
        )
        raise HTTPException(
            status_code=status,
            detail={"error_code": exc.code, "message": str(exc)},
        ) from exc

    return {"status": "success", "route": route.to_dict()}


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


@app.get("/api/food/saved-addresses/")
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




def _companion_agent_turn(
    payload: FoodQueryRequest | AgentQueryRequest,
    user: str,
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
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and item.get("content")
    ]

    execute_tools = (
        payload.execute_tools
        if payload.execute_tools is not None
        else (
            payload.execute_zomato
            if payload.execute_zomato is not None
            else settings.EXECUTE_TOOLS_BY_DEFAULT
        )
    )

    user_geo: dict[str, Any] | None = None
    if payload.geo is not None:
        user_geo = payload.geo.model_dump(exclude_none=True)

    # Pass session_state through as opaque IDs (executor validates keys).
    session_state: dict[str, str] | None = None
    if isinstance(payload.session_state, dict) and payload.session_state:
        session_state = {
            k: str(v)
            for k, v in payload.session_state.items()
            if isinstance(k, str) and isinstance(v, (str, int))
        }

    # Multi-restaurant memory carried forward from prior turns. Frontends
    # echo back whatever ``session_restaurants`` they received in the last
    # response; we sanitize it here so the executor only sees well-formed
    # entries (defensive: an old browser could send junk).
    session_restaurants: list[dict[str, Any]] | None = None
    raw_restaurants = getattr(payload, "session_restaurants", None)
    if isinstance(raw_restaurants, list) and raw_restaurants:
        clean: list[dict[str, Any]] = []
        for entry in raw_restaurants:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("res_id")
            name = entry.get("name")
            if isinstance(rid, str) and rid.strip() and isinstance(name, str) and name.strip():
                clean.append({**entry, "res_id": rid.strip(), "name": name.strip()})
        session_restaurants = clean or None

    # Saved-address memory carried forward from prior turns. Same shape as
    # ``session_restaurants`` — sanitize so the executor only sees rows with
    # a real address_id; everything else (label, address_line, …) is
    # passed through opaquely for the planner's nudge context.
    session_addresses: list[dict[str, Any]] | None = None
    raw_addresses = getattr(payload, "session_addresses", None)
    if isinstance(raw_addresses, list) and raw_addresses:
        clean_addr: list[dict[str, Any]] = []
        for entry in raw_addresses:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id") or entry.get("addressId") or entry.get("id")
            if aid is None:
                continue
            sid = str(aid).strip()
            if not sid:
                continue
            clean_addr.append({**entry, "address_id": sid})
        session_addresses = clean_addr or None

    # Variant-choice deferral state — only ever set when the previous
    # turn skipped a Swiggy ``update_food_cart`` for a multi-option item.
    pending_variant_choice: list[dict[str, Any]] | None = None
    raw_pvc = getattr(payload, "pending_variant_choice", None)
    if isinstance(raw_pvc, list) and raw_pvc:
        clean_pvc: list[dict[str, Any]] = []
        for entry in raw_pvc:
            if not isinstance(entry, dict):
                continue
            iid = entry.get("item_id")
            if not isinstance(iid, str) or not iid.strip():
                continue
            clean_pvc.append(entry)
        pending_variant_choice = clean_pvc or None

    session_movies: list[dict[str, Any]] | None = None
    raw_movies = getattr(payload, "session_movies", None)
    if isinstance(raw_movies, list) and raw_movies:
        clean_movies: list[dict[str, Any]] = []
        for entry in raw_movies:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("movie_id")
            name = entry.get("name")
            if isinstance(mid, str) and mid.strip() and isinstance(name, str) and name.strip():
                clean_movies.append({**entry, "movie_id": mid.strip(), "name": name.strip()})
        session_movies = clean_movies or None

    session_theatres: list[dict[str, Any]] | None = None
    raw_theatres = getattr(payload, "session_theatres", None)
    if isinstance(raw_theatres, list) and raw_theatres:
        clean_theatres: list[dict[str, Any]] = []
        for entry in raw_theatres:
            if not isinstance(entry, dict):
                continue
            tid = entry.get("theatre_id")
            name = entry.get("name")
            if isinstance(tid, str) and tid.strip() and isinstance(name, str) and name.strip():
                clean_theatres.append({**entry, "theatre_id": tid.strip(), "name": name.strip()})
        session_theatres = clean_theatres or None

    # ``session_menu`` — sanitize so the executor only sees rows with a real
    # ``variant_id`` + ``item_name``. Everything else (addon_groups, variants,
    # price, etc.) is passed through verbatim because it was emitted by our
    # own executor on a previous turn and we trust its shape.
    session_menu: list[dict[str, Any]] | None = None
    raw_menu = getattr(payload, "session_menu", None)
    if isinstance(raw_menu, list) and raw_menu:
        clean_menu: list[dict[str, Any]] = []
        for entry in raw_menu:
            if not isinstance(entry, dict):
                continue
            vid = entry.get("variant_id")
            name = entry.get("item_name") or entry.get("name")
            if not isinstance(vid, str) or not vid.strip():
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            clean_menu.append({**entry, "variant_id": vid.strip(), "item_name": name.strip()})
        session_menu = clean_menu or None

    try:
        return run_agent_turn(
            user_message=payload.message.strip(),
            address_id=payload.address_id,
            execute_tools=execute_tools,
            user_id=user,
            llm=llm_cfg,
            conversation_history=recent_history,
            user_geo=user_geo,
            session_state=session_state,
            session_restaurants=session_restaurants,
            session_addresses=session_addresses,
            session_movies=session_movies,
            session_theatres=session_theatres,
            session_menu=session_menu,
            pending_variant_choice=pending_variant_choice,
        )
    except CustomMcpNeedsDiscoveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc


def _message_requires_delivery_address(message: str) -> bool:
    text = f" {(message or '').strip().lower()} "
    delivery_words = {
        " order ",
        " buy ",
        " deliver ",
        " delivery ",
        " restaurant ",
        " restaurants ",
        " menu ",
        " cart ",
        " pizza ",
        " biryani ",
        " burger ",
        " dosa ",
        " milk ",
        " bread ",
        " eggs ",
        " atta ",
        " groceries ",
        " grocery ",
        " instamart ",
        " zepto ",
        " swiggy ",
        " zomato ",
    }
    return any(word in text for word in delivery_words)


@app.post("/api/agent/query/")
def agent_query_endpoint(
    payload: AgentQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    return _companion_agent_turn(payload, user)


@app.post("/api/food/query/")
def food_agent_full(
    payload: FoodQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not payload.address_id.strip() and _message_requires_delivery_address(payload.message):
        raise HTTPException(status_code=422, detail="address_id is required for delivery requests")
    return _companion_agent_turn(payload, user)
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


@app.post("/api/reminders/google-calendar/")
def create_google_calendar_reminder(
    payload: GoogleCalendarReminderRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    result = execute_reminder_calendar(
        user_message=payload.message,
        user_id=user,
        timezone_name=payload.timezone,
        calendar_id=payload.calendar_id,
        email_reminder_minutes=payload.email_reminder_minutes,
        popup_reminder_minutes=payload.popup_reminder_minutes,
        store_reminder=payload.store_reminder,
    )
    if not result.get("ok"):
        code = str(result.get("error_code") or "")
        detail = str(result.get("error") or "Calendar request failed.")
        if code in ("parse_error", "invalid_input", "not_connected", "not_configured"):
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=502, detail=detail)
    return {
        "status": "success",
        "parsed": result.get("parsed"),
        "calendar_event": result.get("calendar_event"),
        "reminder": result.get("reminder"),
    }


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
    
    if reminder:
        # Fetch member to get phone number for WhatsApp notification
        member = MemberRepository().get(payload.member_id)
        if member and member.get("phone_number"):
            from app.services.whatsapp.client import WhatsAppClient
            title = reminder.get("title", "Reminder")
            description = reminder.get("description", "")
            msg = f"🔔 *{title}*"
            if description:
                msg += f"\n{description}"
            
            # If it's a confirmation-required reminder, add quick reply buttons/instructions
            if reminder.get("requires_confirmation"):
                msg += "\n\nReply 'yes' to complete or 'no' to snooze."

            WhatsAppClient().send_text_message(member["phone_number"], msg)

    return {
        "status": "ok",
        "triggered": bool(reminder),
        "triggered_at": datetime.utcnow().isoformat() + "Z",
    }


handler = Mangum(app, lifespan="off")
