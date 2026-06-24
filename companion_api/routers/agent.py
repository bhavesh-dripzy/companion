"""Agent turn endpoints (thin web layer over `core.agent.pipeline`).

- POST /api/agent/turn          — classic request/response (the whole turn in one JSON).
- POST /api/agent/turn/stream   — Server-Sent Events: the turn's narration + cards stream out in
  completion order (`say` / `cards` / `done`), so the UI plays them like a live, typed conversation.

Both run the SAME pipeline; the streaming one just passes an `emit` sink that forwards each event.
The transport (SSE here) lives entirely in this file — the pipeline is transport-agnostic, so a
future Lambda-response-stream / WebSocket swaps in here without touching any agent code.
Auth via the existing Cognito dependency.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
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


def _persist(user: str, chat_id: str, payload: TurnRequest, result: dict[str, Any]) -> None:
    # Persist to the same DynamoDB store the sidebar reads (best-effort, never blocks the reply).
    from companion_api.turn_persistence import persist_turn

    persist_turn(
        user=user,
        chat_id=result.get("chat_id") or chat_id,
        user_text=payload.message or "",
        assistant_text=result.get("assistant_message") or "",
        provider=(result.get("ui_state") or {}).get("active_provider"),
        attachments=result.get("attachments"),
    )


@router.post("/api/agent/turn")
async def agent_turn(payload: TurnRequest, user: str = Depends(get_current_user)) -> dict[str, Any]:
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
    _persist(user, chat_id, payload, result)
    return result


@router.post("/api/agent/turn/stream")
async def agent_turn_stream(payload: TurnRequest, user: str = Depends(get_current_user)):
    """Stream the turn as SSE. Each frame is `data: {json}\\n\\n`; the terminal frame is
    `{"type":"done","result":{…}}` (the same payload the non-streaming endpoint returns)."""
    from core.agent.pipeline import run_turn

    chat_id = (payload.chat_id or "").strip() or f"c_{uuid.uuid4().hex[:8]}"
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict[str, Any]) -> None:
        await queue.put(ev)

    async def runner() -> None:
        try:
            result = await run_turn(
                user_id=user,
                chat_id=chat_id,
                message=payload.message or "",
                mode=payload.mode or "text",
                client_action=payload.client_action,
                address_ids=payload.address_ids,
                emit=emit,
            )
            _persist(user, chat_id, payload, result)
            await queue.put({"type": "done", "result": result})
        except Exception as e:  # noqa: BLE001 — surface a clean terminal error to the client
            await queue.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put(None)  # sentinel → close the stream

    task = asyncio.create_task(runner())

    async def gen():
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            # Client gone? Let the turn FINISH in the background — it still persists, and we never
            # cancel mid-tool-call (that would leave the shared MCP connection half-read and wedge
            # the next request). Just retrieve the result so no "exception never retrieved" warning.
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering so frames flush immediately
        },
    )
