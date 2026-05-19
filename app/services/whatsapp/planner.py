from __future__ import annotations

import json
import logging
from typing import Any

from app.services.food_llm_router import complete_json_object
from app.services.llm_config import ResolvedLlm
from companion_api.dynamodb import MemberRepository

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a WhatsApp Message Planner. Your goal is to extract the recipient and the message content from the user's request.

You have access to a list of family members:
{family_members_json}

If the user mentions a name, find the matching member from the list. If no name is mentioned but a relation is implied (e.g. 'my son'), try to match it or ask for clarification.

Return exactly:
{{
  "intent": "send_whatsapp",
  "recipient_phone": "phone_number_with_country_code",
  "recipient_name": "name",
  "message": "content of the message",
  "reasoning": "why this was chosen"
}}

If you cannot find the recipient, return:
{{
  "intent": "clarify_recipient",
  "reasoning": "Could not find a member named X in the family."
}}
"""

def plan_whatsapp_message(*, user_message: str, user_id: str, llm: ResolvedLlm) -> dict[str, Any]:
    """Plan a WhatsApp message by identifying the recipient from the user's family."""
    repo = MemberRepository()
    current_user = repo.get(user_id)
    if not current_user or not current_user.get("family_id"):
        return {
            "intent": "error",
            "reasoning": "User or family not found."
        }
    
    family_members = repo.list_by_family(current_user["family_id"])
    # Remove the current user from the list to avoid messaging oneself by default
    others = [m for m in family_members if m["member_id"] != user_id]
    
    family_json = json.dumps([{"name": m["name"], "role": m["role"], "phone": m["phone_number"]} for m in others], indent=2)
    
    try:
        plan = complete_json_object(
            user_message=user_message,
            system_prompt=SYSTEM_PROMPT.format(family_members_json=family_json),
            api_key=llm.api_key,
            model=llm.model,
            provider=llm.provider,
            base_url=llm.base_url,
        )
        return plan
    except Exception as e:
        logger.exception("Failed to plan WhatsApp message")
        return {"intent": "error", "reasoning": str(e)}
