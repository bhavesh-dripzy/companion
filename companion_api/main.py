from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

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
from companion_api.secrets import secret_or_env

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
    llm_cfg, err = resolve_llm_from_env(secret_or_env("FOOD_ROUTER_PROVIDER"))
    if err or not llm_cfg:
        raise HTTPException(status_code=503, detail=err or "LLM not configured.")

    try:
        plan = route_food_query(
            user_message=payload.message.strip(),
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
            provider=llm_cfg.provider,
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
