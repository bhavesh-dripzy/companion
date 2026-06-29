"""Flights domain — Google Flights via SerpApi (read-only, no booking).

Flow (2 model calls/turn: shared router + flights_brain):
  1) flights_brain → structured params (IATA + dates) or a clarifying `ask`; the only domain LLM
  2) code         → SerpApi google_flights fetch; code owns the facts (the options + prices)
  3) render       → flight cards + a short grounded line

Round trips are a two-step flow: the first search shows outbound options (each with a
departure_token); we stash them so a "return flights for the ₹X one" follow-up resolves the token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.agent.debug import log, short
from core.agent.events import Emit, cards, noop, say
from core.agent.llm import flights_brain
from core.agent.providers.serp.client import SerpBudgetExceeded, search
from core.agent.providers.serp.flights import extract_flights, flights_params

DOMAIN = "flights"
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


def _summary(route: str, flights: list[dict[str, Any]], lowest: Any) -> str:
    floor = lowest or min((f["price"] for f in flights if f.get("price")), default=None)
    cheap = f" — sabse sasta ₹{floor:,}" if isinstance(floor, int) else ""
    return (
        f"{route} ke liye {len(flights)} flights mile{cheap} 👆 "
        "(book karne ke liye uss app par jaana hoga). Return ya kisi flight ka detail chahiye toh batao."
    )


async def handle(state, intent: dict[str, Any], emit: Emit = noop) -> dict[str, Any]:
    message = intent.get("message", "")
    shown = state.serp_session.get("flights") or []
    brief = [
        {
            "price": f.get("price"),
            "airlines": f.get("airlines"),
            "departure_token": f.get("departure_token"),
        }
        for f in shown
        if f.get("departure_token")
    ][:6]

    brain = await flights_brain(
        {
            "message": message,
            "history": state.recent_history(),
            "today": _today(),
            "shown_flights": brief,
        }
    )
    if brain["ack"]:
        await emit(say(brain["ack"]))

    ready = bool(brain["departure_token"]) or bool(
        brain["departure_id"] and brain["arrival_id"] and brain["outbound_date"]
    )
    if brain["ask"] or not ready:
        return _result(brain["ask"] or "Kahaan se kahaan, aur kis date jaana hai?")
    if brain["outbound_date"] and brain["outbound_date"] < _today():
        return _result("Woh date toh nikal chuki — koi aane wali date batao.")

    args = {
        "departure_id": brain["departure_id"],
        "arrival_id": brain["arrival_id"],
        "outbound_date": brain["outbound_date"],
        "return_date": brain["return_date"] or None,
        "departure_token": brain["departure_token"] or None,
        "adults": brain["adults"] or None,
    }
    log.debug("FLIGHTS args=%s", short(args))
    try:
        data = extract_flights(await search(flights_params(args)))
    except SerpBudgetExceeded:
        return _result("Aaj ka flights quota khatam ho gaya — thodi der baad try karo.")
    except Exception as e:
        log.debug("FLIGHTS fetch failed: %s", e)
        return _result("Abhi flights laana mushkil ho raha hai — thodi der baad try karo.")

    flights = data.get("flights") or []
    if not flights:
        return _result("Is route/date pe koi flight nahi mila — koi aur date try karein?")

    state.serp_session["flights"] = flights  # remember for the return-leg follow-up
    route = f"{args['departure_id']} → {args['arrival_id']}"
    attachment: dict[str, Any] = {
        "kind": "flight_cards",
        "provider": "serp",
        "source": "flights.search",
        "flight_options": flights,
        "route": route,
        "outbound_date": args["outbound_date"],
    }
    if args["return_date"]:
        attachment["return_date"] = args["return_date"]
    if args["departure_token"]:
        attachment["leg"] = "return"
    if data.get("lowest_price"):
        attachment["lowest_price"] = data["lowest_price"]
    # Departure-time preference (e.g. "before 10am") → pre-applied as a filter in the card UI.
    if brain.get("depart_before"):
        attachment["depart_before"] = brain["depart_before"]
    if brain.get("depart_after"):
        attachment["depart_after"] = brain["depart_after"]
    await emit(cards([attachment], provider="serp"))
    return _result(_summary(route, flights, data.get("lowest_price")), [attachment])
