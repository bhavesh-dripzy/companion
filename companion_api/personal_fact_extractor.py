"""Narrow, single-turn personal-fact extraction for the immediate cache write (Tier-1).

Distinct from `leadgen.extractor`: that one runs off-path on the worker and does the
full graph extraction (facts + interests + leads). This one runs ON the request path
and extracts ONLY durable personal columns, so the agent has them on the very next
turn instead of waiting for the async pipeline. Same LLM client, much smaller prompt.

Office/home locations are captured as INFORMATIONAL facts ("book a cab to my office"
needs to know where the office is) — they are never used as delivery addresses, which
still come only from the user's saved provider addresses.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# The cache personal columns this extractor may fill (mirror of the graph User props).
_PERSONAL_COLUMNS = (
    "name",
    "age",
    "city",
    "language",
    "phone",
    "email",
    "occupation",
    "family_size",
    "family_composition",
    "family_names",
    "office_location",
    "home_location",
)

_MAX_PREFERENCES = 20
_MAX_PREFERENCE_LEN = 80

PERSONAL_FACT_SYSTEM_PROMPT = """
You extract DURABLE PERSONAL FACTS and PREFERENCES about the user from a single
conversation turn. Return ONLY things the user clearly stated or that are clearly
implied about THEM or their immediate family — never guesses, never facts about
unrelated third parties, and never specific products they want to buy right now.

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
- family_names: immediate family members' names with their relation, comma-separated
  (e.g. "wife Priya, son Aarav") — only when the user names them
- office_location: where the user's office / workplace is, as they stated it
  (e.g. "DLF Cyber City Tower B, Gurugram")
- home_location: the user's home area / locality as they stated it
  (e.g. "Sector 45, Gurgaon")
- preferences: a list of SHORT durable preference phrases about how THEY like things
  — dietary ("vegetarian", "no onion garlic"), tastes ("loves spicy food", "prefers
  filter coffee"), habits/style ("pays via UPI", "likes concise answers"), or topics
  they follow ("follows cricket"). NOT one-off product purchases or shopping lists.

Do NOT invent values. A location counts only when the user actually states where it
is — "book a cab to my office" alone names no place. If the turn reveals nothing
durable, return an empty object.

Output MUST be a JSON object with only the fields you found, e.g.:
{"occupation": "doctor", "office_location": "Cyber City, Gurugram",
 "preferences": ["vegetarian", "follows cricket"]}
"""


def _coerce(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in ("family_composition", "family_names") and isinstance(value, list | tuple):
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
        pref = str(item or "").strip()[:_MAX_PREFERENCE_LEN]
        key = pref.lower()
        if pref and key not in seen:
            seen.add(key)
            out.append(pref)
    return out[:_MAX_PREFERENCES]


async def extract_personal_facts(
    *,
    user_message: str,
    assistant_message: str,
    complete_json: Callable[..., Awaitable[dict[str, Any]]],
    llm: Any,
) -> dict[str, Any]:
    """Single narrow LLM call → dict of personal columns (+ `preferences` list when
    stated). Possibly empty. Fail-open."""
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
    facts = normalize_personal_facts(raw)
    preferences = normalize_preferences(raw)
    if preferences:
        facts["preferences"] = preferences
    return facts
