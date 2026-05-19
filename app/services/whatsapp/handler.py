from __future__ import annotations

import logging
from typing import Any

from companion_api import settings
from companion_api.dynamodb import MemberRepository
from app.services.whatsapp.client import WhatsAppClient
from app.services.agents.pipeline import run_agent_turn
from app.services.llm_config import resolve_llm_from_env
from companion_api.secrets import secret_or_env

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

def _process_incoming_message(msg: dict[str, Any]) -> None:
    sender_phone = msg.get("from")  # e.g. "919773687877"
    if not sender_phone:
        return

    # Extract text content
    msg_type = msg.get("type")
    user_message = ""
    if msg_type == "text":
        user_message = msg.get("text", {}).get("body", "").strip()
    elif msg_type == "interactive":
        # Handle buttons/lists if needed
        interactive = msg.get("interactive", {})
        if interactive.get("type") == "button_reply":
            user_message = interactive.get("button_reply", {}).get("title", "")
    
    if not user_message:
        logger.info("Ignoring non-text/empty WhatsApp message from %s", sender_phone)
        return

    # 1. Identify Member
    repo = MemberRepository()
    member = repo.get_by_phone(sender_phone)
    if not member:
        logger.warning("WhatsApp message from unknown number: %s", sender_phone)
        # Optional: Send a 'welcome' or 'who are you' message
        # WhatsAppClient().send_text_message(sender_phone, "Hi! I don't recognize this number. Please register in the Companion app.")
        return

    member_id = member["member_id"]
    
    # 2. Check for pending reminder responses (yes/no)
    lowered = user_message.lower()
    if lowered in {"yes", "no", "done", "snooze"}:
        from companion_api.reminders import ReminderScheduler
        scheduler = ReminderScheduler()
        pending = scheduler.repo.list_pending(member_id)
        if pending:
            # Assume the reply is for the most recent pending reminder
            reminder = pending[0]
            response_map = {"yes": "yes", "done": "yes", "no": "no", "snooze": "no"}
            resp = response_map.get(lowered, "no")
            try:
                scheduler.respond(member_id=member_id, reminder_id=reminder["reminder_id"], response=resp)
                msg = "Got it! I've updated your reminder." if resp == "yes" else "Okay, I'll remind you again in a bit."
                WhatsAppClient().send_text_message(sender_phone, msg)
                return
            except Exception:
                logger.exception("Failed to respond to reminder via WhatsApp")

    # 3. Run Agent Turn
    # We need to resolve an LLM. Defaulting to Gemini as per project preference.
    llm_cfg, err = resolve_llm_from_env(secret_or_env("FOOD_ROUTER_PROVIDER"))
    if err or not llm_cfg:
        logger.error("LLM configuration error for WhatsApp: %s", err)
        WhatsAppClient().send_text_message(sender_phone, "Sorry, I'm having trouble thinking right now. Please try again later.")
        return

    try:
        # Note: address_id is required by run_agent_turn for food queries.
        # For WhatsApp, we might need a default address_id per member or ask for it.
        # For now, let's try to pass None and see if the agent handles it.
        result = run_agent_turn(
            user_message=user_message,
            address_id=None, # Should ideally be fetched from Member preferences
            execute_tools=True,
            user_id=member_id,
            llm=llm_cfg,
            conversation_history=[], # We could fetch recent messages from MESSAGES_TABLE
        )
        
        assistant_message = result.get("assistant_message") or "I processed your request but have no reply."
        
        # 3. Send Response Back
        WhatsAppClient().send_text_message(sender_phone, assistant_message)
        
    except Exception:
        logger.exception("Error in WhatsApp agent turn")
        WhatsAppClient().send_text_message(sender_phone, "Something went wrong while processing your request.")
