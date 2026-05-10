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
    from app.services.zomato_mcp_executor import _mcp_remote_config_dir, repo_root_from_settings

    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node_binary = secret_or_env("NODE_BINARY", "node")

    cfg_dir = _mcp_remote_config_dir(repo_root_from_settings())
    token_files: list[str] = []
    if cfg_dir.exists():
        token_files = [p.name for p in cfg_dir.iterdir() if p.is_file()]

    has_tokens = len(token_files) > 0
    url_set = bool(mcp_url.strip())

    if has_tokens:
        status = "ready"
        detail = "MCP URL configured and OAuth tokens present — ready to connect."
    elif url_set:
        status = "needs_auth"
        detail = (
            "MCP URL is set but no OAuth tokens found. Copy ~/.mcp-auth from a "
            "machine where Zomato MCP works in Cursor into this server's .mcp-auth "
            "directory, then redeploy."
        )
    else:
        status = "not_configured"
        detail = "ZOMATO_MCP_URL is not set. Add it to the APP_SECRETS_JSON secret."

    return {
        "status": status,
        "detail": detail,
        "mcp_url": mcp_url if url_set else None,
        "node_binary": node_binary,
        "has_oauth_tokens": has_tokens,
        "mcp_remote_config_dir": str(cfg_dir),
    }


@app.post("/api/mcp/test/")
def mcp_test(_user: str = Depends(get_current_user)):
    """Actually try to connect to Zomato MCP and return a diagnostic report."""
    from app.services.zomato_mcp_executor import _mcp_remote_config_dir, repo_root_from_settings

    cfg_dir = _mcp_remote_config_dir(repo_root_from_settings())
    has_tokens = False
    token_files: list[str] = []
    if cfg_dir.exists():
        token_files = [p.name for p in cfg_dir.iterdir() if p.is_file()]
        has_tokens = len(token_files) > 0

    mcp_url = secret_or_env("ZOMATO_MCP_URL", "https://mcp-server.zomato.com/mcp")
    node_binary = secret_or_env("NODE_BINARY", "node")

    report: dict[str, Any] = {
        "mcp_url": mcp_url,
        "node_binary": node_binary,
        "mcp_remote_config_dir": str(cfg_dir),
        "has_oauth_tokens": has_tokens,
    }

    if not has_tokens:
        report["status"] = "needs_auth"
        report["detail"] = (
            "No OAuth tokens found. On a machine where Zomato MCP works in Cursor, "
            "copy ~/.mcp-auth (or $MCP_REMOTE_CONFIG_DIR) into the Lambda's .mcp-auth "
            "directory. Then redeploy or set the tokens via the admin API."
        )
        return report

    # Try a quick connection
    try:
        from app.services.zomato_mcp_executor import run_tool_plan_sync
        result = run_tool_plan_sync(
            [{"tool": "get_saved_addresses_for_user", "arguments": {}}],
            mcp_url=mcp_url,
            node_binary=node_binary,
        )
        report["test_result"] = result.get("ok", False)
        if result.get("ok"):
            report["status"] = "connected"
            report["detail"] = "Successfully connected to Zomato MCP."
            if result.get("steps"):
                report["address_count"] = len(
                    [s.get("result", {}).get("data", {}).get("result", {}).get("addresses", [])
                     for s in result.get("steps", [])
                     if s.get("tool", "").startswith("get_saved_addresses")]
                )
        else:
            report["status"] = "auth_error"
            report["detail"] = result.get("error_detail", "Unknown error during MCP connection.")
    except Exception as e:
        report["status"] = "error"
        report["detail"] = f"{type(e).__name__}: {e}"

    return report


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
