"""Flights + Hotels domains — pure extractors + handler wiring (no live SerpApi / LLM)."""

from __future__ import annotations

import asyncio

from core.agent.domains import flights_agent, hotels_agent
from core.agent.providers.serp.flights import extract_flights, flights_params
from core.agent.providers.serp.hotels import extract_hotels, hotels_params


class _State:
    user_id = "u1"

    def __init__(self):
        self.serp_session: dict = {}

    def recent_history(self):
        return []


# ── flights extractor ──────────────────────────────────────────────────────
def test_flights_params_oneway_vs_round():
    assert (
        flights_params({"departure_id": "del", "arrival_id": "bom", "outbound_date": "2099-01-01"})[
            "type"
        ]
        == "2"
    )
    rt = flights_params(
        {
            "departure_id": "DEL",
            "arrival_id": "BOM",
            "outbound_date": "2099-01-01",
            "return_date": "2099-01-05",
        }
    )
    assert rt["type"] == "1" and rt["return_date"] == "2099-01-05" and rt["currency"] == "INR"


def test_extract_flights():
    payload = {
        "best_flights": [
            {
                "price": 6438,
                "total_duration": 130,
                "flights": [
                    {
                        "airline": "IndiGo",
                        "departure_airport": {"id": "DEL", "time": "06:00"},
                        "arrival_airport": {"id": "BOM", "time": "08:10"},
                    }
                ],
                "departure_token": "tok1",
            }
        ],
        "other_flights": [{"price": 6750, "flights": []}],
        "price_insights": {"lowest_price": 6348},
    }
    out = extract_flights(payload)
    assert out["lowest_price"] == 6348
    assert len(out["flights"]) == 2
    assert out["flights"][0]["price"] == 6438
    assert out["flights"][0]["airlines"] == ["IndiGo"]
    assert out["flights"][0]["departure_token"] == "tok1"
    assert out["flights"][0]["stops"] == 0


def _fbrain(**over):
    base = {
        "departure_id": "DEL",
        "arrival_id": "BOM",
        "outbound_date": "2099-01-01",
        "return_date": "",
        "adults": 0,
        "departure_token": "",
        "ack": "dekh raha hoon…",
        "ask": "",
    }
    base.update(over)

    async def _f(_ctx):
        return base

    return _f


def test_flights_handle_success(monkeypatch):
    async def fake_search(_p):
        return {
            "best_flights": [{"price": 6438, "flights": [], "departure_token": "t"}],
            "price_insights": {"lowest_price": 6348},
        }

    monkeypatch.setattr(flights_agent, "flights_brain", _fbrain())
    monkeypatch.setattr(flights_agent, "search", fake_search)
    out = asyncio.run(flights_agent.handle(_State(), {"message": "del to bom"}))
    assert out["domain"] == "flights"
    att = out["attachments"][0]
    assert att["kind"] == "flight_cards" and att["route"] == "DEL → BOM"
    assert att["flight_options"][0]["price"] == 6438


def test_flights_handle_asks_when_missing(monkeypatch):
    monkeypatch.setattr(
        flights_agent,
        "flights_brain",
        _fbrain(departure_id="", arrival_id="", outbound_date="", ask="Kahaan se kahaan?"),
    )
    out = asyncio.run(flights_agent.handle(_State(), {"message": "flight chahiye"}))
    assert out["attachments"] == [] and "Kahaan" in out["say"]


def test_flights_handle_error_graceful(monkeypatch):
    async def boom(_p):
        raise RuntimeError("no key")

    monkeypatch.setattr(flights_agent, "flights_brain", _fbrain())
    monkeypatch.setattr(flights_agent, "search", boom)
    out = asyncio.run(flights_agent.handle(_State(), {"message": "del to bom"}))
    assert out["attachments"] == [] and "flights" in out["say"].lower()


# ── hotels extractor ───────────────────────────────────────────────────────
def test_hotels_params():
    p = hotels_params(
        {"query": "Goa", "check_in_date": "2099-01-01", "check_out_date": "2099-01-03"}
    )
    assert p["engine"] == "google_hotels" and p["q"] == "Goa" and p["currency"] == "INR"


def test_extract_hotels():
    payload = {
        "properties": [
            {
                "name": "Radisson",
                "rate_per_night": {"lowest": "₹5,509"},
                "overall_rating": 4.9,
                "reviews": 1200,
                "amenities": ["Pool", "Spa", "Gym", "Bar", "Wifi", "Parking"],
                "images": [{"thumbnail": "http://img"}],
                "property_token": "pt",
            }
        ]
    }
    rows = extract_hotels(payload)
    assert rows[0]["name"] == "Radisson"
    assert rows[0]["rate_per_night"] == "₹5,509"
    assert rows[0]["rating"] == 4.9
    assert len(rows[0]["amenities"]) == 5  # capped


def _hbrain(**over):
    base = {
        "query": "Goa",
        "check_in_date": "2099-01-01",
        "check_out_date": "2099-01-03",
        "adults": 0,
        "ack": "dhoondh raha hoon…",
        "ask": "",
    }
    base.update(over)

    async def _f(_ctx):
        return base

    return _f


def test_hotels_handle_success(monkeypatch):
    async def fake_search(_p):
        return {"properties": [{"name": "Radisson", "rate_per_night": {"lowest": "₹5,509"}}]}

    monkeypatch.setattr(hotels_agent, "hotels_brain", _hbrain())
    monkeypatch.setattr(hotels_agent, "search", fake_search)
    out = asyncio.run(hotels_agent.handle(_State(), {"message": "goa hotel"}))
    att = out["attachments"][0]
    assert att["kind"] == "hotel_cards" and att["keyword"] == "Goa"
    assert att["hotel_options"][0]["name"] == "Radisson"


def test_hotels_handle_asks_when_missing(monkeypatch):
    monkeypatch.setattr(
        hotels_agent,
        "hotels_brain",
        _hbrain(query="", check_in_date="", check_out_date="", ask="Kahaan aur kis date?"),
    )
    out = asyncio.run(hotels_agent.handle(_State(), {"message": "hotel chahiye"}))
    assert out["attachments"] == [] and "Kahaan" in out["say"]
