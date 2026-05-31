from __future__ import annotations
import logging
from app.services.llm_config import resolve_llm_from_env
from app.services.food_llm_router import complete_json_object
from app.services.leadgen.schema import ExtractionResult, ProfileFact, Interest, Lead

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert entity extraction assistant for a per-user personal knowledge-graph and lead generation pipeline.
Your job is to analyze the conversation history between a user and an AI assistant, and extract:
1. Durable profile facts:
   - Home address, work address, other address
   - Name, age, city, language preferences, etc.
2. User interests:
   - Likes, cuisines, dish types, shopping interests, travel patterns, electronics interests, etc.
   - Assign a category (food, electronics, furniture, salon_services, travel, grocery, etc.) and a weight (0.0 to 1.0) indicating interest level.
3. Open leads (commercial intents where the assistant has no fulfillment path today, meaning they are looking to buy or query products/services):
   - Category: food, electronics, furniture, salon_services, travel, finance, health, etc.
   - Product: specific product description (e.g., "1.5 ton split AC", "wooden sofa set").
   - Attrs: Key-value attributes like brand, size, specifications, etc.
   - Budget signal: "low", "mid", "high", or null.
   - Intent score: 0.0 to 1.0 (how close they are to purchasing).
   - Confidence: 0.0 to 1.0.

Only extract facts, interests, and leads that are explicitly mentioned or clearly implied. Do not hallucinate.
Do not extract standard food delivery orders as leads unless the user is researching/expressing intent for future/large purchases or looking for recommendations.

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
    # Resolve LLM configuration from environment
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
        json_data = complete_json_object(
            user_message=user_prompt,
            system_prompt=SYSTEM_PROMPT,
            api_key=llm_cfg.api_key,
            model=llm_cfg.model,
            provider=llm_cfg.provider,
            base_url=llm_cfg.base_url,
        )

        # Parse into ExtractionResult
        profile_facts = []
        for fact in json_data.get("profile_facts", []):
            try:
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
