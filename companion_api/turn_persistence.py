"""Persist a completed agent turn: chat lazy-create + title, messages, leadgen.

The single place every turn surface (text chat, async worker, voice) lands its
conversation history. Lazy-creates the chat on first turn — auto-titled from the
opening message via `derive_chat_title` — appends both messages, bumps the
sidebar metadata, and enqueues the leadgen turn event. Best-effort: persistence
errors are swallowed (logged) so they can never break a live reply.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from companion_api.leadgen_queue import send_turn_event
from companion_api.messages_repo import ChatRepository, MessageRepository, derive_chat_title

logger = logging.getLogger(__name__)


def persist_turn(
    *,
    user: str,
    chat_id: str | None,
    user_text: str,
    assistant_text: str,
    task_type: str | None = None,
    provider: str | None = None,
    session_addresses: Any = None,
    user_geo: dict | None = None,
    fallback_title: str = "New chat",
) -> str | None:
    """Persist one user/assistant exchange; returns the resolved chat_id."""
    resolved_chat_id = (chat_id or "").strip() or None
    try:
        chat_repo = ChatRepository()
        existing = chat_repo.get(user, resolved_chat_id) if resolved_chat_id else None
        created = False
        if existing is None:
            resolved_chat_id = resolved_chat_id or str(uuid.uuid4())
            chat_repo.create(
                member_id=user,
                chat_id=resolved_chat_id,
                title=derive_chat_title(user_text or fallback_title),
            )
            created = True

        turn_id = str(uuid.uuid4())
        msg_repo = MessageRepository()
        if user_text:
            msg_repo.create(
                member_id=user,
                chat_id=resolved_chat_id,
                message_id=f"{turn_id}-user",
                role="user",
                text=user_text,
                task_type=task_type,
                provider=provider,
            )
        if assistant_text:
            msg_repo.create(
                member_id=user,
                chat_id=resolved_chat_id,
                message_id=f"{turn_id}-assistant",
                role="assistant",
                text=assistant_text,
                task_type=task_type,
                provider=provider,
            )
        chat_repo.touch(
            member_id=user,
            chat_id=resolved_chat_id,
            last_message_preview=(assistant_text or "").strip(),
            title=derive_chat_title(user_text) if created and user_text else None,
            increment_count=(1 if user_text else 0) + (1 if assistant_text else 0),
        )
    except Exception:
        logger.exception("turn persistence failed (user=%s)", user)

    try:
        send_turn_event(
            user_id=user,
            message_id=str(uuid.uuid4()),
            user_message=user_text,
            assistant_message=assistant_text,
            task_type=task_type,
            routed_provider=provider,
            session_addresses=session_addresses,
            user_geo=user_geo,
            member_id=user,
        )
    except Exception:
        logger.exception("turn leadgen enqueue failed (user=%s)", user)

    return resolved_chat_id
