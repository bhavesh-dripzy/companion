"""Chat management routes: list / create / messages / rename / delete.

Extracted verbatim from main.py (#29). Backed by the DynamoDB message store;
the two projection helpers are chats-only and live here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from companion_api.auth import get_current_user
from companion_api.messages_repo import ChatRepository, MessageRepository

router = APIRouter()


def _message_history_item(item: dict[str, Any]) -> dict[str, Any] | None:
    role = item.get("role")
    text = item.get("text")
    if role not in {"user", "assistant"} or not isinstance(text, str):
        return None

    response: dict[str, Any] = {
        "role": role,
        "content": text,
        "created_at": item.get("created_at"),
        "message_id": item.get("created_at_message_id"),
    }
    provider = item.get("provider")
    if isinstance(provider, str) and provider:
        response["provider"] = provider

    meta = [
        value for value in [item.get("task_type"), provider] if isinstance(value, str) and value
    ]
    if meta:
        response["meta"] = meta

    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        blob = metadata.get("attachments_json")
        if isinstance(blob, str) and blob:
            try:
                attachments = json.loads(blob)
            except ValueError:
                attachments = None
            if isinstance(attachments, list) and attachments:
                response["attachments"] = attachments

    return response


def _chat_summary(item: dict[str, Any]) -> dict[str, Any] | None:
    """Project a Dynamo ``chat#…`` row into the slim shape the sidebar wants."""
    chat_id = item.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id.strip():
        return None
    return {
        "chat_id": chat_id,
        "title": (item.get("title") or "New chat") or "New chat",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "last_message_at": item.get("last_message_at"),
        "last_message_preview": item.get("last_message_preview") or "",
        "message_count": int(item.get("message_count") or 0),
    }


@router.get("/api/chats/")
def list_chats(
    limit: int = Query(default=50, ge=1, le=200),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """List the signed-in user's chats, most recently active first."""
    try:
        items = ChatRepository().list_chats(user, limit=limit)
        chats = [c for it in items if (c := _chat_summary(it)) is not None]
        return {"chats": chats, "count": len(chats)}
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load chats: {type(exc).__name__}: {exc}"
        ) from exc


@router.post("/api/chats/")
def create_chat(
    user: str = Depends(get_current_user),
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Explicitly create a new chat (lazy-create via /api/agent/query/ is the normal path)."""
    try:
        title = None
        if isinstance(body, dict):
            raw_title = body.get("title")
            if isinstance(raw_title, str) and raw_title.strip():
                title = raw_title.strip()
        chat = ChatRepository().create(member_id=user, chat_id=str(uuid.uuid4()), title=title)
        summary = _chat_summary(chat)
        if summary is None:
            raise HTTPException(status_code=500, detail="Chat row malformed after create")
        return summary
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to create chat: {type(exc).__name__}: {exc}"
        ) from exc


@router.get("/api/chats/{chat_id}/messages/")
def list_chat_messages(
    chat_id: str,
    limit: int = Query(default=80, ge=1, le=200),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the chronological message log for one chat."""
    try:
        items = MessageRepository().list_for_chat(user, chat_id, limit=limit)
        messages = [m for item in items if (m := _message_history_item(item)) is not None]
        return {"messages": messages, "count": len(messages), "chat_id": chat_id}
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to load chat messages: {type(exc).__name__}: {exc}"
        ) from exc


@router.patch("/api/chats/{chat_id}/")
def rename_chat(
    chat_id: str,
    body: dict[str, Any] = Body(...),
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Rename a chat's sidebar title. Body: ``{"title": "..."}``."""
    raw_title = body.get("title") if isinstance(body, dict) else None
    if not isinstance(raw_title, str) or not raw_title.strip():
        raise HTTPException(status_code=400, detail="`title` is required")
    try:
        updated = ChatRepository().rename(member_id=user, chat_id=chat_id, title=raw_title.strip())
        if updated is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        summary = _chat_summary(updated)
        if summary is None:
            raise HTTPException(status_code=500, detail="Chat row malformed after rename")
        return summary
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to rename chat: {type(exc).__name__}: {exc}"
        ) from exc


@router.delete("/api/chats/{chat_id}/")
def delete_chat(
    chat_id: str,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a chat and every message it contains (messages first, metadata last)."""
    try:
        deleted_msgs = MessageRepository().delete_chat_messages(user, chat_id)
        existed = ChatRepository().delete(user, chat_id)
        return {"chat_id": chat_id, "deleted_messages": deleted_msgs, "chat_existed": existed}
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to delete chat: {type(exc).__name__}: {exc}"
        ) from exc
