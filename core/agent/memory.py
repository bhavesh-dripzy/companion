"""User memory for the agent — capture durable personal facts from chat, recall them for prompts.

Two halves, both fail-open (a memory hiccup must never break a turn):

* `capture(user_id, message)` — one narrow LLM call extracts durable personal columns
  (name / age / city / occupation / family / office & home locations / preferences)
  from the user's message and merges them into the DynamoDB profile cache. The pipeline
  kicks this off CONCURRENTLY with the domain handler (so it adds ~no latency) and
  awaits it before returning (so it completes on Lambda, where fire-and-forget dies).

* `recall(user_id)` — loads the profile cache row and renders WHO the user is + WHAT
  THEY PREFER as a compact, prompt-ready block. Privacy-light: phone / email / age are
  stored but never surfaced to prompts. Empty string when nothing is known.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Messages shorter than this can't state a durable fact ("haan", "2 kar do", "ok").
_MIN_CAPTURE_LEN = 10

_SKIP = {"", "not mentioned", "none", "n/a", "unknown", "null"}
_MAX_PREFERENCES = 10

# Rendered identity fields, in display order. Phone / email / age are deliberately
# absent (privacy-light) even though the extractor stores them.
_IDENTITY_FIELDS = (
    ("Name", "name"),
    ("City", "city"),
    ("Occupation", "occupation"),
    ("Family", "family_composition"),
    ("Family names", "family_names"),
    ("Office", "office_location"),
    ("Home area", "home_location"),
    ("Speaks", "language"),
)


def _clean(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in _SKIP else text


def render_user_memory(profile: dict[str, Any] | None) -> str:
    """Compact memory block for prompts; '' when nothing personal is known."""
    if not isinstance(profile, dict):
        return ""
    identity = "; ".join(
        f"{label}: {v}" for label, key in _IDENTITY_FIELDS if (v := _clean(profile.get(key)))
    )
    seen: set[str] = set()
    prefs: list[str] = []
    for item in profile.get("preferences") or []:
        pref = _clean(item)
        if pref and pref.lower() not in seen:
            seen.add(pref.lower())
            prefs.append(pref)
    lines = [f"- {identity}"] if identity else []
    if prefs:
        lines.append(f"- Preferences: {'; '.join(prefs[:_MAX_PREFERENCES])}")
    if not lines:
        return ""
    return (
        "WHAT YOU KNOW ABOUT THIS USER (use only when relevant to their request; "
        "personalize naturally and never recite this back as a list):\n" + "\n".join(lines)
    )


def worth_capturing(message: str) -> bool:
    return len((message or "").strip()) >= _MIN_CAPTURE_LEN


async def recall(user_id: str) -> str:
    """Profile cache row → prompt block. Fail-open to ''."""
    if not user_id:
        return ""
    try:
        from companion_api.profile_cache import ProfileCacheRepository

        profile = await asyncio.to_thread(lambda: ProfileCacheRepository().get(user_id))
        return render_user_memory(profile)
    except Exception:
        logger.warning("user-memory recall failed", exc_info=True)
        return ""


def _persist_facts(user_id: str, facts: dict[str, Any]) -> None:
    """Merge extracted columns into the profile cache (scalars overwrite; preferences union)."""
    from companion_api.profile_cache import ProfileCacheRepository

    facts = dict(facts)
    prefs = facts.pop("preferences", None)
    repo = ProfileCacheRepository()
    if facts:
        repo.merge_personal(user_id, facts)
    if prefs:
        repo.merge_preferences(user_id, list(prefs))


async def capture(user_id: str, message: str) -> None:
    """Extract durable personal facts from this message and persist them. Fail-open."""
    if not user_id or not worth_capturing(message):
        return
    try:
        from companion_api.personal_fact_extractor import extract_personal_facts
        from core.llm.client import complete_json_object
        from core.llm.config import resolve_llm_from_env

        llm, reason = resolve_llm_from_env()
        if llm is None:
            logger.debug("user-memory capture skipped: %s", reason)
            return
        facts = await extract_personal_facts(
            user_message=message,
            assistant_message="",
            complete_json=complete_json_object,
            llm=llm,
        )
        if not facts:
            return
        await asyncio.to_thread(_persist_facts, user_id, facts)
        logger.debug("user-memory captured for %s: %s", user_id, sorted(facts))
    except Exception:
        logger.warning("user-memory capture failed", exc_info=True)
