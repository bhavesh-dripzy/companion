"""SerpApi data-provider plugin (issue #46) — param mapping, lean extraction,
cache/budget guards, routing, registration.

Engine extractors mirror shapes observed in LIVE validation (2026-06-10, all
five engines + the round-trip departure_token flow returned real data).
"""

import asyncio

import pytest

from core.contracts.tool_call import ToolCall
from core.pipeline.orchestration import parse_routing
from core.providers.base import ExecCtx
from core.providers.plugins.serp import client as serp_client
from core.providers.plugins.serp import engines
from core.providers.plugins.serp.provider import SerpProvider

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_client():
    serp_client.reset_for_tests()
    yield
    serp_client.reset_for_tests()


def test_flights_params_one_way_vs_round_trip():
    one_way = engines.flights_params(
        {"departure_id": "del", "arrival_id": "bom", "outbound_date": "2026-06-20"}
    )
    assert one_way["type"] == "2" and one_way["departure_id"] == "DEL"
    assert "return_date" not in one_way
    round_trip = engines.flights_params(
        {
            "departure_id": "DEL",
            "arrival_id": "BOM",
            "outbound_date": "2026-06-20",
            "return_date": "2026-06-23",
            "departure_token": "tok123",
        }
    )
    assert round_trip["type"] == "1" and round_trip["departure_token"] == "tok123"


def test_extract_flights_lean_rows_and_round_trip_note():
    payload = {
        "best_flights": [
            {
                "price": 6228,
                "total_duration": 265,
                "departure_token": "tok",
                "flights": [
                    {
                        "departure_airport": {"id": "DEL", "time": "2026-06-20 05:10"},
                        "arrival_airport": {"id": "AMD", "time": "2026-06-20 06:35"},
                        "airline": "IndiGo",
                        "flight_number": "6E 123",
                    },
                    {
                        "departure_airport": {"id": "AMD", "time": "2026-06-20 07:30"},
                        "arrival_airport": {"id": "BOM", "time": "2026-06-20 09:35"},
                        "airline": "IndiGo",
                        "flight_number": "6E 456",
                    },
                ],
            }
        ],
        "price_insights": {"lowest_price": 6228, "price_level": "low"},
    }
    data = engines.extract_flights(payload)
    flight = data["flights"][0]
    assert flight["price"] == 6228 and flight["stops"] == 1
    assert flight["airlines"] == ["IndiGo"]
    assert flight["legs"][0]["from"] == "DEL" and flight["legs"][1]["to"] == "BOM"
    assert data["lowest_price"] == 6228
    assert "departure_token" in flight and data["note"], "round-trip 2-step flow surfaced"


def test_extract_hotels_places_products_youtube():
    hotels = engines.extract_hotels(
        {
            "properties": [
                {
                    "name": "Baga Sunshine",
                    "rate_per_night": {"lowest": "₹2,613"},
                    "overall_rating": 3.65,
                }
            ]
        }
    )
    assert hotels["hotels"][0]["rate_per_night"] == "₹2,613"

    places = engines.extract_places(
        {
            "local_results": [
                {
                    "title": "Apollo Pharmacy",
                    "rating": 4.9,
                    "type": "Pharmacy",
                    "address": "Hauz Khas",
                }
            ]
        }
    )
    assert places["places"][0]["name"] == "Apollo Pharmacy"

    products = engines.extract_products(
        {
            "organic_results": [
                {"title": "AirPods Pro", "price": {"raw": "₹23,990"}, "rating": 4.3, "asin": "B0X"}
            ]
        }
    )
    assert products["products"][0]["price"] == "₹23,990"

    videos = engines.extract_youtube(
        {
            "video_results": [
                {
                    "title": "falooda recipe",
                    "channel": {"name": "Hebbars Kitchen"},
                    "views": 1774917,
                }
            ]
        }
    )
    assert videos["videos"][0]["channel"] == "Hebbars Kitchen"


def test_places_params_prefer_named_location_over_gps():
    named = engines.places_params(
        {"query": "atms", "location_text": "Hauz Khas"}, {"latitude": 28.5, "longitude": 77.2}
    )
    assert named["q"] == "atms near Hauz Khas" and "ll" not in named
    gps = engines.places_params({"query": "atms"}, {"latitude": 28.5, "longitude": 77.2})
    assert gps["ll"] == "@28.5,77.2,14z"


def test_client_cache_serves_repeat_lookups(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    calls = {"n": 0}

    class _Resp:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"ok": True}

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(serp_client.httpx, "AsyncClient", _Client)
    asyncio.run(serp_client.search({"engine": "youtube", "search_query": "x"}))
    asyncio.run(serp_client.search({"engine": "youtube", "search_query": "x"}))
    assert calls["n"] == 1, "second identical lookup must come from the TTL cache"


def test_client_budget_guard(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setenv("SERPAPI_DAILY_BUDGET", "1")
    monkeypatch.setenv("SERPAPI_CACHE_TTL_SECONDS", "0")

    class _Resp:
        status_code = 200
        content = b"{}"

        @staticmethod
        def json():
            return {"ok": True}

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(serp_client.httpx, "AsyncClient", _Client)
    asyncio.run(serp_client.search({"engine": "youtube", "search_query": "a"}))
    with pytest.raises(serp_client.SerpBudgetExceeded):
        asyncio.run(serp_client.search({"engine": "youtube", "search_query": "b"}))


def test_budget_exhaustion_is_a_tool_error_not_a_crash(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setenv("SERPAPI_DAILY_BUDGET", "1")
    monkeypatch.setenv("SERPAPI_CACHE_TTL_SECONDS", "0")

    async def _boom(params):
        raise serp_client.SerpBudgetExceeded("budget gone")

    import core.providers.plugins.serp.provider as provider_mod

    monkeypatch.setattr(provider_mod, "search", _boom)
    result = asyncio.run(
        SerpProvider().run([ToolCall(tool="youtube_search", arguments={"query": "x"})], ExecCtx())
    )
    tr = result.results[0]
    assert not tr.ok and "budget" in (tr.error_detail or "")


def test_instagram_gated_behind_flag(monkeypatch):
    monkeypatch.delenv("SERP_INSTAGRAM_ENABLED", raising=False)
    names = {t.name for t in SerpProvider().declared_tools()}
    assert "instagram_profile" not in names and len(names) == 5
    monkeypatch.setenv("SERP_INSTAGRAM_ENABLED", "true")
    names = {t.name for t in SerpProvider().declared_tools()}
    assert "instagram_profile" in names


def test_data_lookup_routes_solely_to_serp():
    decision = parse_routing({"task_type": "data_lookup", "reasoning": "flights"}, None)
    assert decision.task_type == "data_lookup" and decision.providers == ["serp"]


def test_serp_registered_with_real_schemas():
    from core.registry.bootstrap import build_registry

    registry = build_registry()
    assert registry.get("serp") is not None
    tools = registry.tools.for_provider("serp")
    flights = next(t for t in tools if t.name == "search_flights")
    assert "departure_token" in flights.params["properties"]


def test_extract_instagram_real_profile_results_shape():
    payload = {
        "profile_results": {
            "full_name": "Virat Kohli",
            "username": "virat.kohli",
            "followers": 273733780,
            "following": 279,
            "posts_count": 1057,
            "biography": "Carpediem!",
            "is_verified": True,
            "is_private": False,
        }
    }
    data = engines.extract_instagram(payload)
    assert data["name"] == "Virat Kohli" and data["followers"] == 273733780
    assert data["verified"] is True


def test_missing_required_args_fail_before_spending_a_request(monkeypatch):
    async def _never(params):
        raise AssertionError("billed request must not happen on invalid args")

    import core.providers.plugins.serp.provider as provider_mod

    monkeypatch.setattr(provider_mod, "search", _never)
    result = asyncio.run(
        SerpProvider().run(
            [ToolCall(tool="search_flights", arguments={"departure_id": "DEL"})], ExecCtx()
        )
    )
    tr = result.results[0]
    assert not tr.ok
    assert "arrival_id" in (tr.error_detail or "") and "outbound_date" in (tr.error_detail or "")
