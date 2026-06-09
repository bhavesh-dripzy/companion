from __future__ import annotations

import asyncio
import logging

from core.llm.client import complete_json_object
from core.llm.config import resolve_llm_from_env
from leadgen.schema import ExtractionResult, Interest, Lead, ProfileFact

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert entity extraction assistant for a per-user personal knowledge-graph and lead generation pipeline.
Your job is to analyze the conversation history between a user and an AI assistant, and extract a clean, hierarchical user memory graph:
User
├── profile facts
├── places
├── interest categories
│   └── specific interests
└── commercial leads
    └── category

Extract:
1. Durable profile facts:
   - Name, age, city, language preferences, email, phone, etc.
   - Do NOT extract home/work/delivery addresses or locations. Delivery
     addresses come ONLY from the user's saved addresses on the provider,
     never from conversation. Ignore any street/area/address the user mentions.
2. User interests:
   - Likes, cuisines, dish types, shopping interests, travel patterns, electronics interests, etc.
   - Assign a broad category and a specific detail. Prefer one of these category names:
     food, grocery, electronics, furniture, home_services, salon_services, travel,
     entertainment, finance, health, fashion, education, automotive, pets, other.
   - Use the category for hierarchy and the detail for the leaf node.
   - Assign a weight (0.0 to 1.0) indicating interest level.
3. Open leads (commercial intents where the assistant has no fulfillment path today, meaning they are looking to buy or query products/services):
   - Category: use the same broad category taxonomy as interests.
   - Product: specific product description (e.g., "1.5 ton split AC", "wooden sofa set").
   - Attrs: Key-value attributes like brand, size, specifications, etc.
   - Budget signal: "low", "mid", "high", or null.
   - Intent score: 0.0 to 1.0 (how close they are to purchasing).
   - Confidence: 0.0 to 1.0.

Only extract facts, interests, and leads that are explicitly mentioned or clearly implied. Do not hallucinate.
Do not extract standard food delivery orders as leads unless the user is researching/expressing intent for future/large purchases or looking for recommendations.
Do not create duplicate interests with the same category and detail in one response.
Do not put long sentences into category. Category should be a stable bucket; detail/product should carry specificity.

Your output MUST be a JSON object of this exact schema:
{
  "profile_facts": [
    {"type": "work_address", "value": "Sector 13, DLF, Gurugram", "confidence": 0.95}
  ],
  "interests": [
    {"category": "food", "detail": "ramen", "weight": 0.8}
  ],
  "leads": [
    {
      "category": "electronics",
      "product": "1.5 ton split AC",
      "attrs": {"brand": "Daikin", "type": "split"},
      "budget_signal": "mid",
      "intent_score": 0.8,
      "confidence": 0.9
    }
  ]
}
"""


def extract_from_conversation(
    *,
    user_id: str,
    user_message: str,
    assistant_message: str,
    recent_history: list[dict[str, str]],
) -> ExtractionResult:
    """Invokes LLM with recent history and current turn message to extract profile facts, interests and leads."""
    # Resolve LLM configuration from environment (core's resolver — same order/defaults)
    llm_cfg, err = resolve_llm_from_env()
    if err or not llm_cfg:
        logger.error(f"Failed to resolve LLM configuration: {err}")
        return ExtractionResult()

    # Format the messages transcript for the prompt
    transcript_lines = []
    for turn in recent_history:
        role = turn.get("role", "").upper()
        content = turn.get("text") or turn.get("content") or ""
        if role and content:
            transcript_lines.append(f"{role}: {content}")

    # Include current turn explicitly in case it's not in the history yet
    transcript_lines.append(f"USER: {user_message}")
    transcript_lines.append(f"ASSISTANT: {assistant_message}")

    transcript = "\n".join(transcript_lines)

    user_prompt = f"""
Analyze this conversation transcript and extract facts, interests, and leads:

TRANSCRIPT:
{transcript}
"""

    try:
        json_data = asyncio.run(
            complete_json_object(
                user_message=user_prompt,
                system_prompt=SYSTEM_PROMPT,
                llm=llm_cfg,
            )
        )

        # Parse into ExtractionResult
        profile_facts = []
        for fact in json_data.get("profile_facts", []):
            try:
                # Addresses come ONLY from the user's saved addresses on the
                # provider apps (Zomato/Swiggy), never from conversation.
                # Drop any address/location facts the LLM extracts.
                ftype = str(fact.get("type", "")).strip().lower()
                if "address" in ftype or ftype in {"home", "office", "work", "location", "place"}:
                    continue
                profile_facts.append(ProfileFact(**fact))
            except Exception:
                continue

        interests = []
        for interest in json_data.get("interests", []):
            try:
                interests.append(Interest(**interest))
            except Exception:
                continue

        leads = []
        for lead in json_data.get("leads", []):
            try:
                leads.append(Lead(**lead))
            except Exception:
                continue

        return ExtractionResult(
            profile_facts=profile_facts,
            interests=interests,
            leads=leads,
        )
    except Exception as e:
        logger.exception(f"Error during LLM extraction: {e}")
        return ExtractionResult()
