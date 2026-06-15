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
You extract DURABLE PERSONAL FACTS about the user from a single conversation turn.
Return ONLY facts the user clearly stated or that are clearly implied about THEM —
never guesses, never facts about other people, products, or places.

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

Do NOT extract home/work/delivery addresses or street locations.
Do NOT invent values. If the turn contains no personal facts, return an empty object.

Output MUST be a JSON object with only the fields you found, e.g.:
{"occupation": "doctor", "city": "Gurugram", "family_composition": "spouse, child"}
"""


def _coerce(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key == "family_composition" and isinstance(value, (list, tuple)):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    return text or None


def normalize_personal_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only known personal columns with non-empty values, as scalars (graph parity)."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _PERSONAL_COLUMNS:
        coerced = _coerce(key, raw.get(key))
        if coerced is not None:
            out[key] = coerced
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
