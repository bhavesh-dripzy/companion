from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from mangum import Mangum
from pydantic import BaseModel

from companion_api.auth import (
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
from app.services.zomato_mcp_executor import run_tool_plan_sync
from companion_api import settings
from companion_api.dynamodb import ReminderRepository
from companion_api.models import (
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


@app.post("/api/login/")
def login(payload: LoginRequest) -> LoginResponse:
    if not validate_admin_login(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    token = create_access_token(payload.username)
    return LoginResponse(access_token=token, username=payload.username)


@app.get("/api/auth/status/")
def auth_status(user: str = Depends(get_current_user)):
    return {"authenticated": True, "username": user}


# ---------------------------------------------------------------------------
# MCP status
# ---------------------------------------------------------------------------


@app.get("/api/mcp/status/")
def mcp_status(_user: str = Depends(get_current_user)):
    from app.services.zomato_mcp_executor import diagnose_mcp_environment, get_mcp_token_status, repo_root_from_settings

    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node_binary = secret_or_env("NODE_BINARY", "node")
    url_set = bool(mcp_url.strip())

    root = repo_root_from_settings()
    token_status = get_mcp_token_status(root, mcp_url=mcp_url)
    diagnostics = diagnose_mcp_environment(root, mcp_url=mcp_url, node_binary=node_binary)
    has_local = token_status["has_local_tokens"]
    has_secret = token_status["has_secret_tokens"]

    if not url_set:
        status = "not_configured"
        detail = "ZOMATO_MCP_URL is not set. Add it in Settings / Secrets."
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
            "Authorize Zomato locally with mcp-remote, then import the complete ~/.mcp-auth directory."
        )

    return {
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
def mcp_initiate_oauth(_user: str = Depends(get_current_user)):
    """Run mcp-remote briefly to capture the OAuth authorization URL.
    
    Zomato MCP requires OAuth via browser. Since Lambda cannot host a persistent
    localhost server to receive the OAuth callback, this endpoint runs mcp-remote
    briefly to capture the authorization URL. The user must:
    1. Click this to get the URL
    2. Open it in their browser and authorize
    3. After authorization, tokens will be in .mcp-auth on their LOCAL machine
    4. Upload those tokens via POST /api/mcp/tokens/
    """
    from app.services.zomato_mcp_executor import (
        capture_mcp_oauth_url,
        repo_root_from_settings,
    )

    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node_binary = secret_or_env("NODE_BINARY", "node")

    result = capture_mcp_oauth_url(
        repo_root=repo_root_from_settings(),
        mcp_url=mcp_url,
        node_binary=node_binary,
        timeout_seconds=12,
    )
    result["mcp_url"] = mcp_url
    return result


@app.post("/api/mcp/test/")
def mcp_test(_user: str = Depends(get_current_user)):
    """Auto-restore tokens from Secrets Manager, then test connection."""
    from app.services.zomato_mcp_executor import (
        get_mcp_token_status,
        restore_mcp_auth_tokens,
        repo_root_from_settings,
        run_tool_plan_sync,
    )

    root = repo_root_from_settings()
    token_status = get_mcp_token_status(root)

    # Auto-restore from Secrets Manager if needed
    if not token_status["has_local_tokens"] and token_status["has_secret_tokens"]:
        restored = restore_mcp_auth_tokens(root)
        if not restored:
            return {
                "status": "needs_tokens",
                "detail": "Tokens found in Secrets Manager but could not be restored to disk.",
            }
        token_status = get_mcp_token_status(root)

    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node_binary = secret_or_env("NODE_BINARY", "node")

    if not token_status["has_local_tokens"] and not token_status["has_secret_tokens"]:
        return {
            "status": "needs_tokens",
            "detail": (
                "No OAuth tokens available. Since Lambda's filesystem is ephemeral, "
                "upload your Zomato MCP tokens via Settings → Upload Tokens. "
                "They'll be stored in AWS Secrets Manager and auto-restored on every cold start."
            ),
            "mcp_url": mcp_url,
        }

    # Try connection
    try:
        result = run_tool_plan_sync(
            [{"tool": "get_saved_addresses_for_user", "arguments": {}}],
            mcp_url=mcp_url,
            node_binary=node_binary,
            repo_root=root,
        )
        if result.get("ok"):
            return {
                "status": "connected",
                "detail": "Successfully connected to Zomato MCP.",
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


# ---------------------------------------------------------------------------
# MCP token upload (for Lambda ephemeral filesystem fix)
# ---------------------------------------------------------------------------


class McpTokenUpload(BaseModel):
    files: dict[str, str]  # filename → content


@app.post("/api/mcp/tokens/")
def mcp_upload_tokens(payload: McpTokenUpload, _user: str = Depends(get_current_user)):
    """Upload Zomato MCP OAuth token files. Stored in Secrets Manager, auto-restored on cold starts."""
    from app.services.zomato_mcp_executor import (
        _mcp_remote_config_dir,
        _safe_relative_token_path,
        persist_mcp_auth_tokens,
        repo_root_from_settings,
    )

    root = repo_root_from_settings()
    cfg_dir = _mcp_remote_config_dir(root)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = []
    for name, content in payload.files.items():
        rel = _safe_relative_token_path(name)
        if rel is None or not content:
            skipped.append(name)
            continue
        dest = cfg_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written += 1

    persisted = persist_mcp_auth_tokens(root)
    return {
        "status": "ok",
        "written_locally": written,
        "skipped": skipped,
        "persisted_to_secrets": list(persisted.keys()),
        "detail": (
            f"{written} file(s) saved. Tokens are persisted and will restore on cold starts. "
            "Test the connection to verify the Zomato session."
        ),
    }


@app.get("/api/mcp/tokens/status/")
def mcp_tokens_status(_user: str = Depends(get_current_user)):
    from app.services.zomato_mcp_executor import get_mcp_token_status, repo_root_from_settings
    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    return get_mcp_token_status(repo_root_from_settings(), mcp_url=mcp_url)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.post("/api/send-message/")
def send_whatsapp_message(payload: SendMessageRequest) -> dict[str, Any]:
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
def food_agent_full(payload: FoodQueryRequest) -> dict[str, Any]:
    provider_override = (payload.provider or "").strip()
    llm_cfg, err = resolve_llm_from_env(
        provider_override or secret_or_env("FOOD_ROUTER_PROVIDER")
    )
    if err or not llm_cfg:
        raise HTTPException(status_code=503, detail=err or "LLM not configured.")

    try:
        plan = route_food_query(
            user_message=payload.message.strip(),
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
            provider=llm_cfg.provider,
            base_url=llm_cfg.base_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e

    execute_zomato = (
        settings.EXECUTE_ZOMATO_BY_DEFAULT
        if payload.execute_zomato is None
        else payload.execute_zomato
    )
    zomato_raw: dict[str, Any] | None = None
    if execute_zomato:
        tool_plan = plan.get("tool_plan") if isinstance(plan, dict) else []
        zomato_raw = run_tool_plan_sync(
            tool_plan if isinstance(tool_plan, list) else [],
            address_id=payload.address_id,
        )
        zomato_summary = build_zomato_summary(zomato_raw)
    else:
        zomato_summary = build_zomato_summary(None)

    out: dict[str, Any] = {
        "status": "success",
        "provider": llm_cfg.provider,
        "food_routing_plan": plan,
        "zomato_summary": zomato_summary,
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
def create_reminder(payload: ReminderCreateRequest) -> dict[str, Any]:
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
def pending_reminders(member_id: str = Query(min_length=1)) -> list[dict[str, Any]]:
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
def respond_reminder(payload: ReminderResponseRequest) -> dict[str, Any]:
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
