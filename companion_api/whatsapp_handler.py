"""Inbound WhatsApp Cloud API webhook handler.

Identifies the member by phone, handles reminder yes/no replies, then runs the
turn through the core pipeline (`run_core_turn_sync`) and replies over the
WhatsApp Cloud API. Relocated from `app/services/whatsapp/handler.py` in the
legacy teardown and rewired off the deleted `run_agent_turn` monolith onto core.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from companion_api.dynamodb import MemberRepository
from companion_api.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)


def handle_whatsapp_webhook_event(payload: dict[str, Any]) -> None:
    """Process incoming WhatsApp Webhook events (Cloud API)."""
    # Webhook structure: entry[] -> changes[] -> value -> messages[]
    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                _process_incoming_message(msg)


def _extract_user_message(msg: dict[str, Any]) -> str:
    msg_type = msg.get("type")
    if msg_type == "text":
        return msg.get("text", {}).get("body", "").strip()
    if msg_type == "interactive":
        interactive = msg.get("interactive", {})
        if interactive.get("type") == "button_reply":
            return interactive.get("button_reply", {}).get("title", "")
    return ""


def _handle_reminder_reply(member_id: str, sender_phone: str, lowered: str) -> bool:
    """Handle a yes/no/done/snooze reply to the most recent pending reminder.

    Returns True when the reply was consumed as a reminder response.
    """
    if lowered not in {"yes", "no", "done", "snooze"}:
        return False
    from companion_api.reminders import ReminderScheduler

    scheduler = ReminderScheduler()
    pending = scheduler.repo.list_pending(member_id)
    if not pending:
        return False
    # Assume the reply is for the most recent pending reminder.
    reminder = pending[0]
    resp = {"yes": "yes", "done": "yes", "no": "no", "snooze": "no"}.get(lowered, "no")
    try:
        scheduler.respond(member_id=member_id, reminder_id=reminder["reminder_id"], response=resp)
        reply = (
            "Got it! I've updated your reminder."
            if resp == "yes"
            else "Okay, I'll remind you again in a bit."
        )
        WhatsAppClient().send_text_message(sender_phone, reply)
        return True
    except Exception:
        logger.exception("Failed to respond to reminder via WhatsApp")
        return False


def _recent_history(member_id: str) -> list[dict[str, str]]:
    try:
        from companion_api.messages_repo import MessageRepository

        items = MessageRepository().list_recent(member_id, limit=8)
        return [{"role": item["role"], "content": item["text"]} for item in items]
    except Exception as exc:
        logger.warning("Failed to load message history for WhatsApp user: %s", exc)
        return []


def _log_turn(
    *,
    member_id: str,
    sender_phone: str,
    user_message: str,
    assistant_message: str,
    result: dict[str, Any],
) -> None:
    """Persist the user+assistant messages and enqueue the leadgen turn event."""
    try:
        from companion_api.leadgen_queue import send_turn_event
        from companion_api.messages_repo import MessageRepository

        turn_id = str(uuid.uuid4())
        task_type = result.get("task_type")
        routed_provider = result.get("routed_provider")

        repo = MessageRepository()
        repo.create(
            member_id=member_id,
            message_id=f"{turn_id}-user",
            role="user",
            text=user_message.strip(),
            task_type=task_type,
            provider=routed_provider,
        )
        repo.create(
            member_id=member_id,
            message_id=f"{turn_id}-assistant",
            role="assistant",
            text=assistant_message,
            task_type=task_type,
            provider=routed_provider,
        )
        send_turn_event(
            user_id=member_id,
            message_id=turn_id,
            user_message=user_message.strip(),
            assistant_message=assistant_message,
            task_type=task_type,
            routed_provider=routed_provider,
            session_addresses=result.get("session_addresses"),
            phone=sender_phone,
            member_id=member_id,
        )
    except Exception as exc:
        logger.exception("Failed to log message or enqueue turn event for WhatsApp: %s", exc)


def _process_incoming_message(msg: dict[str, Any]) -> None:
    sender_phone = msg.get("from")  # e.g. "919773687877"
    if not sender_phone:
        return

    user_message = _extract_user_message(msg)
    if not user_message:
        logger.info("Ignoring non-text/empty WhatsApp message from %s", sender_phone)
        return

    # 1. Identify member.
    member = MemberRepository().get_by_phone(sender_phone)
    if not member:
        logger.warning("WhatsApp message from unknown number: %s", sender_phone)
        return
    member_id = member["member_id"]

    # 2. Pending reminder responses (yes/no/done/snooze) short-circuit the turn.
    if _handle_reminder_reply(member_id, sender_phone, user_message.lower()):
        return

    # 3. Run the turn through the core pipeline (LLM resolution happens inside).
    try:
        from companion_api.core_shadow import run_core_turn_sync

        result = run_core_turn_sync(
            {
                "message": user_message,
                "address_id": None,  # profile-resolver handles home/office keywords
                "execute_tools": True,
                "history": _recent_history(member_id),
            },
            member_id,
        )

        if result.get("status") == "error":
            logger.error("WhatsApp core turn error: %s", result.get("error"))
            WhatsAppClient().send_text_message(
                sender_phone,
                "Sorry, I'm having trouble thinking right now. Please try again later.",
            )
            return

        assistant_message = (
            result.get("assistant_message") or "I processed your request but have no reply."
        )
        _log_turn(
            member_id=member_id,
            sender_phone=sender_phone,
            user_message=user_message,
            assistant_message=assistant_message,
            result=result,
        )
        WhatsAppClient().send_text_message(sender_phone, assistant_message)
    except Exception:
        logger.exception("Error in WhatsApp core turn")
        WhatsAppClient().send_text_message(
            sender_phone, "Something went wrong while processing your request."
        )
