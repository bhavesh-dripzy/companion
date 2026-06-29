"""LLM for the HOTELS domain — the only domain model call (besides the shared router).

Turns the user's message + history into Google-Hotels params (free-text query + resolved dates), or
a short clarifying question (`ask`) when dates are missing. It never writes the final reply.
"""

from __future__ import annotations

from typing import Any

from core.agent.llm.base import _complete

HOTELS_BRAIN_SYSTEM = """\
You plan a Google Hotels search from the user's request. Read message + history + today's date.

CONTEXT (JSON): message, history, today (YYYY-MM-DD).

Output STRICTLY this JSON:
{"query":"<free-text place query>","check_in_date":"YYYY-MM-DD","check_out_date":"YYYY-MM-DD",
 "adults":<int or 0>,"ack":"<tiny Hinglish line>","ask":"<clarifying question or empty>"}

MINIMUM REQUIRED TO SEARCH — you may only fill the params when you have ALL of:
  query (the place)  +  a resolved check_in_date  +  a resolved check_out_date (both real YYYY-MM-DD).
If ANY is missing or you can't pin it down, leave the params EMPTY and set `ask` to a short Hinglish
question requesting EXACTLY what's missing:
  • place known, dates missing/unclear  -> ask only for the dates ("Kis date se kis date tak?")
  • place missing                        -> ask for the place      ("Kahaan ke hotels?")
  • both missing                         -> ask for both
Asking is the correct, expected behavior — never guess to avoid asking.

RULES:
- query: free text describing WHERE ("hotels in Goa", "hotels in Goa near Baga beach"). Keep the
  locality the user named.
- DATES: check_in_date/check_out_date MUST be real YYYY-MM-DD on/after `today`. Resolve relative
  ranges ("is weekend", "kal se 3 din", "next Friday to Sunday") yourself; a duration ("2 nights this
  weekend") -> pick the concrete check-in/out dates from `today`. If the user gave no dates, or you
  can't resolve them to specific calendar dates, leave them EMPTY and ask. NEVER output a past date,
  a guessed date, or any non-YYYY-MM-DD text in a date field.
- adults: the count if stated, else 0.
- When everything required IS present, leave `ask` empty.
- ack: a tiny Hinglish "searching" line ("Hotels dhoondh raha hoon…"). Never put results in it.
"""


async def hotels_brain(ctx: dict[str, Any]) -> dict[str, Any]:
    """One call → hotel search params + ack (+ ask when info is missing)."""
    out = await _complete(HOTELS_BRAIN_SYSTEM, ctx)
    return {
        "query": (out.get("query") or "").strip(),
        "check_in_date": (out.get("check_in_date") or "").strip(),
        "check_out_date": (out.get("check_out_date") or "").strip(),
        "adults": out.get("adults") or 0,
        "ack": (out.get("ack") or "").strip(),
        "ask": (out.get("ask") or "").strip(),
    }
