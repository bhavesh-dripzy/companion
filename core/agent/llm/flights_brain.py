"""LLM for the FLIGHTS domain — the only domain model call (besides the shared router).

Turns the user's message + history into structured Google-Flights params (IATA codes, resolved
dates), or — if something required is missing — a short Hinglish clarifying question (`ask`). It
never writes the final reply: code fetches options and the domain renders cards + a grounded line.
"""

from __future__ import annotations

from typing import Any

from core.agent.llm.base import _complete

FLIGHTS_BRAIN_SYSTEM = """\
You plan a Google Flights search from the user's request. Read message + history + today's date.

CONTEXT (JSON): message, history, today (YYYY-MM-DD), shown_flights (options from the last search,
each with price/airlines/departure_token — present only when the user is following up).

Output STRICTLY this JSON:
{"departure_id":"<IATA>","arrival_id":"<IATA>","outbound_date":"YYYY-MM-DD",
 "return_date":"YYYY-MM-DD or empty","adults":<int or 0>,"departure_token":"<token or empty>",
 "depart_before":"HH:MM or empty","depart_after":"HH:MM or empty",
 "ack":"<tiny Hinglish line>","ask":"<clarifying question or empty>"}

MINIMUM REQUIRED TO SEARCH — you may only fill the params when you have ALL of:
  departure_id  +  arrival_id  +  a resolved outbound_date (a real YYYY-MM-DD).
(A return-leg follow-up is the only exception: it needs just a valid departure_token.)
If ANY required piece is missing or you cannot pin it down, you MUST leave the params EMPTY and set
`ask` to a short Hinglish question requesting EXACTLY what's missing — and nothing more:
  • route known, date missing/unclear   -> ask only for the date  ("Kis date jaana hai?")
  • cities missing                       -> ask for the route      ("Kahaan se kahaan?")
  • both missing                         -> ask for both           ("Kahaan se kahaan, aur kis date?")
Asking is the correct, expected behavior — never guess to avoid asking.

RULES:
- IATA codes: convert city names (Delhi->DEL, Mumbai->BOM, Bengaluru->BLR, Goa->GOI, Hyderabad->HYD,
  Chennai->MAA, Kolkata->CCU, Pune->PNQ, Jaipur->JAI, Ahmedabad->AMD, Lucknow->LKO; international too).
- DATES: outbound_date MUST be a real YYYY-MM-DD on/after `today`. Resolve relative dates ("kal",
  "next Friday", "is weekend") yourself against `today`. If the user gave NO date, or it's vague and
  you can't resolve it to ONE specific calendar date, leave outbound_date EMPTY and ask (per above).
  NEVER output a past date, a guessed date, or any non-YYYY-MM-DD text in a date field.
- return_date ONLY if the user wants a round trip AND you can resolve it to YYYY-MM-DD; else empty.
- adults: the count if stated, else 0.
- RETURN-LEG FOLLOW-UP: if `shown_flights` is present and the user picks one for return legs ("uske
  return", "return flights for the ₹6228 one"), set departure_token to that option's token and KEEP
  the same departure_id/arrival_id/outbound_date/return_date. Otherwise leave departure_token empty.
- DEPARTURE-TIME PREFERENCE (optional, 24h HH:MM): if the user constrains the departure time, set
  depart_before / depart_after. "subah 10 baje se pehle" -> depart_before 10:00. "shaam 6 ke baad" /
  "after 6pm" -> depart_after 18:00. "morning flight" -> depart_before 12:00. "raat ki" -> depart_after
  21:00. Else leave both empty. This is just a preference for the results UI — it NEVER blocks the
  search and is NOT a required field.
- When everything required IS present, leave `ask` empty.
- ack: a tiny Hinglish "searching" line ("Flights dekh raha hoon…"). Never put prices/results in it.
"""


async def flights_brain(ctx: dict[str, Any]) -> dict[str, Any]:
    """One call → structured flight params + ack (+ ask when info is missing)."""
    out = await _complete(FLIGHTS_BRAIN_SYSTEM, ctx)
    return {
        "departure_id": (out.get("departure_id") or "").strip().upper(),
        "arrival_id": (out.get("arrival_id") or "").strip().upper(),
        "outbound_date": (out.get("outbound_date") or "").strip(),
        "return_date": (out.get("return_date") or "").strip(),
        "adults": out.get("adults") or 0,
        "departure_token": (out.get("departure_token") or "").strip(),
        "depart_before": (out.get("depart_before") or "").strip(),
        "depart_after": (out.get("depart_after") or "").strip(),
        "ack": (out.get("ack") or "").strip(),
        "ask": (out.get("ask") or "").strip(),
    }
