"""POST /api/agent/turn — the rebuilt agent endpoint (thin web layer over `core.agent.pipeline`).

Auth via the existing Cognito dependency. The whole turn (route → resolve → execute → narrate)
lives in `core.agent`; this just adapts the HTTP request/response.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from companion_api.auth import get_current_user

router = APIRouter()


class TurnRequest(BaseModel):
    chat_id: str | None = None
    mode: str = "text"                       # "text" | "voice"
    message: str = ""
    client_action: dict[str, Any] | None = None   # deterministic UI tap (bypasses the LLM)
    geo: dict[str, Any] | None = None
    address_ids: dict[str, str] | None = None     # FE-selected delivery address per provider


@router.post("/api/agent/turn")
async def agent_turn(payload: TurnRequest, user: str = Depends(get_current_user)) -> dict[str, Any]:
    from companion_api.turn_persistence import persist_turn
    from core.agent.pipeline import run_turn

    chat_id = (payload.chat_id or "").strip() or f"c_{uuid.uuid4().hex[:8]}"
    result = await run_turn(
        user_id=user,
        chat_id=chat_id,
        message=payload.message or "",
        mode=payload.mode or "text",
        client_action=payload.client_action,
        address_ids=payload.address_ids,
    )

    # Persist the exchange to the same DynamoDB store the sidebar reads, so agent chats show
    # under "Recent Conversations" and survive a restart. Best-effort (never blocks the reply).
    # A UI tap carries no user text → only the assistant turn is saved.
    persist_turn(
        user=user,
        chat_id=result.get("chat_id") or chat_id,
        user_text=payload.message or "",
        assistant_text=result.get("assistant_message") or "",
        provider=(result.get("ui_state") or {}).get("active_provider"),
        attachments=result.get("attachments"),
    )
    return result
