"""Hotels domain — Google Hotels via SerpApi (read-only, no booking).

Flow (2 model calls/turn: shared router + hotels_brain):
  1) hotels_brain → {query, check_in, check_out, adults} or a clarifying `ask`; the only domain LLM
  2) code        → SerpApi google_hotels fetch; code owns the facts
  3) render      → hotel cards + a short grounded line
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.agent.debug import log, short
from core.agent.events import Emit, cards, noop, say
from core.agent.llm import hotels_brain
from core.agent.providers.serp.client import SerpBudgetExceeded, search
from core.agent.providers.serp.hotels import extract_hotels, hotels_params

DOMAIN = "hotels"
_IST = timezone(timedelta(hours=5, minutes=30))


def _today() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _result(say_text: str, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "domain": DOMAIN,
        "status": "ok",
        "error": None,
        "cards": [],
        "attachments": attachments or [],
        "say": say_text,
    }


async def handle(state, intent: dict[str, Any], emit: Emit = noop) -> dict[str, Any]:
    message = intent.get("message", "")
    brain = await hotels_brain(
        {"message": message, "history": state.recent_history(), "today": _today()}
    )
    if brain["ack"]:
        await emit(say(brain["ack"]))

    ready = bool(brain["query"] and brain["check_in_date"] and brain["check_out_date"])
    if brain["ask"] or not ready:
        return _result(brain["ask"] or "Kahaan, aur kis date se kis date tak?")
    if brain["check_in_date"] < _today():
        return _result("Check-in date toh nikal chuki — aane wali date batao.")

    args = {
        "query": brain["query"],
        "check_in_date": brain["check_in_date"],
        "check_out_date": brain["check_out_date"],
        "adults": brain["adults"] or None,
    }
    log.debug("HOTELS args=%s", short(args))
    try:
        hotels = extract_hotels(await search(hotels_params(args)))
    except SerpBudgetExceeded:
        return _result("Aaj ka hotels quota khatam ho gaya — thodi der baad try karo.")
    except Exception as e:
        log.debug("HOTELS fetch failed: %s", e)
        return _result("Abhi hotels laana mushkil ho raha hai — thodi der baad try karo.")

    if not hotels:
        return _result(
            f"'{brain['query']}' ke liye in dates pe koi hotel nahi mila — dates badal ke dekhein?"
        )

    attachment = {
        "kind": "hotel_cards",
        "provider": "serp",
        "source": "hotels.search",
        "keyword": brain["query"],
        "hotel_options": hotels,
    }
    await emit(cards([attachment], provider="serp"))
    return _result(
        f"{brain['query']} ke liye {len(hotels)} hotels mile 👆 — kisi ka detail ya doosri dates chahiye toh batao.",
        [attachment],
    )
