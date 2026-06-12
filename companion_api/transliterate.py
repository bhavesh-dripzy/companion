"""Devanagari → Hinglish romanization for voice transcripts.

Gemini Live's input transcription renders Hindi speech in Devanagari, while the
assistant persona (and the rest of the product) speaks romanized Hinglish. These
helpers convert user transcripts to natural chat-style Hinglish so the chat log,
persisted history, and leadgen pipeline stay in one script. Best-effort by
design: any failure returns the original text.
"""

from __future__ import annotations

import logging
import re

from companion_api.secrets import secret_or_env
from core.llm.client import complete_json_object
from core.llm.config import resolve_llm_from_env

logger = logging.getLogger(__name__)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

_SYSTEM_PROMPT = (
    "You transliterate Hindi text written in Devanagari script into natural, casual "
    "Hinglish — Hindi written in the Roman/Latin alphabet, the way Indians type it in "
    "chat apps (e.g. 'मैं घर जा रहा हूं' → 'main ghar ja raha hoon'). Keep English "
    "words, brand names, proper nouns, and numerals exactly as written. Do NOT "
    "translate to English, do NOT answer the text, do NOT add or drop words — only "
    'change the script. Return JSON: {"romanized": "<transliterated text>"}.'
)


def contains_devanagari(text: str | None) -> bool:
    """True when the text has at least one Devanagari codepoint."""
    return bool(_DEVANAGARI.search(text or ""))


async def romanize_hinglish(text: str) -> str:
    """Transliterate Devanagari text to Hinglish; returns the input unchanged on
    pure-Roman input, missing LLM config, or any call/parse failure."""
    if not contains_devanagari(text):
        return text
    try:
        llm, _ = resolve_llm_from_env()
        if llm is None:
            return text
        out = await complete_json_object(
            user_message=text, system_prompt=_SYSTEM_PROMPT, llm=llm
        )
        romanized = str(out.get("romanized") or "").strip()
        return romanized or text
    except Exception:
        logger.exception("romanize_hinglish failed; keeping original text")
        return text
