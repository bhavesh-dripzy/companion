"""Narrow, single-turn personal-fact extraction for the immediate cache write (Tier-1).

Distinct from `leadgen.extractor`: that one runs off-path on the worker and does the
full graph extraction (facts + interests + leads). This one runs ON the request path
and extracts ONLY durable personal columns, so the agent has them on the very next
turn instead of waiting for the async pipeline. Same LLM client, much smaller prompt.

Addresses are deliberately excluded — delivery addresses come only from the user's
saved provider addresses, never from conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# The cache personal columns this extractor may fill (mirror of the graph User props).
_PERSONAL_COLUMNS = (
    "name", "age", "city", "language", "phone", "email",
    "occupation", "family_size", "family_composition",
)

PERSONAL_FACT_SYSTEM_PROMPT = """
You extract DURABLE PERSONAL FACTS and PREFERENCES about the user from a single
conversation turn. Return ONLY things the user clearly stated or that are clearly
implied about THEM — never guesses, never facts about other people, and never
specific products they want to buy or vendors (those are handled elsewhere).

Extract only these fields (omit any you are not confident about):
- name: the user's name
- age: approximate age or age group
- city: the city the user lives in
- language: preferred language
- phone: phone number
- email: email address
- occupation: job / profession (e.g. "doctor", "software engineer")
- family_size: number of family members
- family_composition: family roles, comma-separated (e.g. "spouse, child, parent")
- preferences: a list of SHORT durable preference phrases about how THEY like things
  — dietary ("vegetarian", "no onion garlic"), tastes ("loves spicy food", "prefers
  filter coffee"), habits/style ("pays via UPI", "likes concise answers"), or topics
  they follow ("follows cricket"). NOT one-off product purchases or shopping lists.

Do NOT extract home/work/delivery addresses or street locations.
Do NOT invent values. If the turn reveals nothing durable, return an empty object.

Output MUST be a JSON object with only the fields you found, e.g.:
{"occupation": "doctor", "city": "Gurugram", "preferences": ["vegetarian", "follows cricket"]}
"""


def _coerce(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key == "family_composition" and isinstance(value, (list, tuple)):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    return text or None


def normalize_preferences(raw: dict[str, Any]) -> list[str]:
    """Clean the freeform preference list — short non-empty phrases, de-duped, capped."""
    value = raw.get("preferences") if isinstance(raw, dict) else None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        key = text.lower()
        if text and len(text) <= 80 and key not in seen:
            seen.add(key)
            out.append(text)
    return out[:10]


def normalize_personal_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """Known personal columns (scalars, graph parity) plus the freeform preference list."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _PERSONAL_COLUMNS:
        coerced = _coerce(key, raw.get(key))
        if coerced is not None:
            out[key] = coerced
    prefs = normalize_preferences(raw)
    if prefs:
        out["preferences"] = prefs
    return out


async def extract_personal_facts(
    *,
    user_message: str,
    assistant_message: str,
    complete_json: Callable[..., Awaitable[dict[str, Any]]],
    llm: Any,
) -> dict[str, Any]:
    """Single narrow LLM call → dict of personal columns (possibly empty). Fail-open."""
    if not (user_message or "").strip():
        return {}
    prompt = (
        "Extract durable personal facts about the user from this turn.\n\n"
        f"USER: {user_message}\nASSISTANT: {assistant_message}"
    )
    try:
        raw = await complete_json(
            user_message=prompt, system_prompt=PERSONAL_FACT_SYSTEM_PROMPT, llm=llm
        )
    except Exception as exc:
        logger.warning("personal-fact extraction failed: %s", exc)
        return {}
    return normalize_personal_facts(raw)
