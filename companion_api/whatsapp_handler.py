"""Inbound WhatsApp Cloud API webhook handler.

Identifies the member by phone and handles reminder yes/no replies. General
turn execution is STUBBED during the backend rebuild (the agent pipeline was
removed); inbound messages get a clean "unavailable" reply until the new
backend reconnects its executor.
"""

from __future__ import annotations

import logging
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

    # 3. Turn execution is STUBBED during the backend rebuild. The agent
    # pipeline was removed so the execution architecture can be rebuilt from
    # scratch; reply with a clean "unavailable" so inbound users get a graceful
    # message instead of an error. The new backend reconnects its executor here.
    try:
        WhatsAppClient().send_text_message(
            sender_phone,
            "I'm being rebuilt right now and can't process requests just yet. "
            "Please try again soon.",
        )
    except Exception:
        logger.exception("Error sending WhatsApp reply")
