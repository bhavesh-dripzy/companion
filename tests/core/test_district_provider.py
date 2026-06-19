"""Golden test: the clean `DistrictProvider` builds typed cards from REAL data.

Drives the provider against sanitized real captures (dining / movies / events,
live read-only searches on admin_bhavesh), asserting each discovery surface emits
its own typed attachment + feeds the matching session LRU, reflecting the real
entities the live turns surfaced.
"""

import asyncio
import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.base import ExecCtx
from core.providers.mcp.placeholders import PlaceholderResolver
from core.providers.plugins.district import DistrictProvider

pytestmark = pytest.mark.component

_DIR = Path(__file__).parent / "fixtures/district"


def test_district_seeds_user_coordinates_for_search():
    ctx = ExecCtx(user_geo={"latitude": 28.6139, "longitude": 77.209})
    asyncio.run(DistrictProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__USER_LAT__"] == 28.6139
    assert ctx.placeholders["__USER_LNG__"] == 77.209
    args = PlaceholderResolver().substitute(
        {"location": {"latitude": "__USER_LAT__", "longitude": "__USER_LNG__"}},
        ctx.placeholders,
    )
    assert args == {"location": {"latitude": 28.6139, "longitude": 77.209}}


def test_district_without_geo_leaves_coordinates_unseeded():
    ctx = ExecCtx()
    asyncio.run(DistrictProvider().before_run(session=None, ctx=ctx))
    assert "__USER_LAT__" not in ctx.placeholders


def test_district_seeds_from_resolved_geo_not_raw_text():
    """Location resolution lives in the Execute stage (named locality beats GPS);
    before_run only copies the already-resolved ctx.user_geo into placeholders."""
    ctx = ExecCtx(
        user_geo={"latitude": 28.54, "longitude": 77.25}, requested_location_text="110019"
    )
    asyncio.run(DistrictProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__USER_LAT__"] == 28.54
    assert ctx.placeholders["__USER_LNG__"] == 77.25


def test_district_seeds_from_saved_location_when_no_live_gps():
    """The per-user saved District location (source of truth, carried in the
    session legacy bag) seeds the search coordinates when there's no live GPS."""
    ctx = ExecCtx(session={"selected_district_location": {"latitude": 19.07, "longitude": 72.87}})
    asyncio.run(DistrictProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__USER_LAT__"] == 19.07
    assert ctx.placeholders["__USER_LNG__"] == 72.87


def test_district_saved_location_beats_drifting_gps():
    """Without a user-named locality this turn, the committed saved location wins
    over ambient GPS — a moved phone must not silently shift the search area."""
    ctx = ExecCtx(
        user_geo={"latitude": 28.61, "longitude": 77.20},
        session={"selected_district_location": {"latitude": 19.07, "longitude": 72.87}},
    )
    asyncio.run(DistrictProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__USER_LAT__"] == 19.07
    assert ctx.placeholders["__USER_LNG__"] == 72.87


def test_district_named_locality_beats_saved_location():
    """A locality the user named this turn (geocoded into user_geo by the Execute
    stage) outranks the saved location."""
    ctx = ExecCtx(
        user_geo={"latitude": 12.97, "longitude": 77.59},
        requested_location_text="Bangalore",
        session={"selected_district_location": {"latitude": 19.07, "longitude": 72.87}},
    )
    asyncio.run(DistrictProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__USER_LAT__"] == 12.97
    assert ctx.placeholders["__USER_LNG__"] == 77.59


def test_district_run_needs_location_without_any_geo():
    """No named locality, no saved location, no GPS → return needs_location
    without opening an MCP session (mirrors Zepto's guard)."""
    from core.contracts.tool_call import ToolCall
    from core.contracts.tool_result import ErrorCode

    calls = [ToolCall(tool="search_movies", arguments={"location": {}})]
    result = asyncio.run(DistrictProvider().run(calls, ExecCtx()))
    assert result.ok is False
    assert result.error is ErrorCode.NEEDS_LOCATION
    assert result.results[0].tool == "search_movies"


def _tr(name: str) -> ToolResult:
    fix = json.loads((_DIR / f"{name}.json").read_text())
    return ToolResult(
        tool=fix["tool"],
        provider="district",
        ok=not (fix["result"] or {}).get("is_error"),
        arguments=fix.get("arguments") or {},
        data=fix["result"],
    )


def _result(*names: str) -> ProviderResult:
    return ProviderResult(provider="district", results=[_tr(n) for n in names])


def test_builds_typed_cards_per_discovery_surface():
    cards = DistrictProvider().build_attachments(_result("dining", "movies", "events"))
    by_kind = {c.kind: c.to_wire() for c in cards}
    assert set(by_kind) == {"dining_cards", "movie_cards", "event_cards"}
    assert all(c["provider"] == "district" for c in by_kind.values())

    dining = by_kind["dining_cards"]["dining_restaurants"]
    assert any(r["res_id"] == "21844770" and r["name"] == "Ivoryy Fusion Bar" for r in dining)
    movies = by_kind["movie_cards"]["movies"]
    assert any(m["movie_id"] == "187151" and m["name"] == "The Odyssey" for m in movies)
    events = by_kind["event_cards"]["events"]
    assert any(e["event_id"] == "EV716085507533" for e in events)


def test_merge_feeds_per_entity_session_lrus():
    merged = DistrictProvider().merge({}, _result("dining", "movies", "events"))
    assert any(r["res_id"] == "21844770" for r in merged.get("session_restaurants", []))
    assert any(m["movie_id"] == "187151" for m in merged.get("session_movies", []))
    assert any(e["event_id"] == "EV716085507533" for e in merged.get("session_events", []))
    # Shows/details specs carry no session key — only the real entity lists appear.
    assert "session_shows" not in merged


def test_builds_reservation_card_and_session_reservation():
    # Booking is mutating; drive a representative create_table_reservation_booking envelope.
    env = {
        "is_error": False,
        "data": {
            "result": {
                "booking_id": "BK12345",
                "res_name": "Ivoryy Fusion Bar",
                "booking_status": "CONFIRMED",
                "final_amount": 500,
                "confirmation_url": "https://district.in/booking/BK12345",
            }
        },
    }
    tr = ToolResult(
        tool="create_table_reservation_booking",
        provider="district",
        ok=True,
        arguments={"res_id": "21844770", "party_size": 4, "slot_time": "20:00"},
        data=env,
    )
    provider = DistrictProvider()
    result = ProviderResult(provider="district", results=[tr])
    cards = [c for c in provider.build_attachments(result) if c.kind == "reservation_card"]
    assert len(cards) == 1
    snap = cards[0].to_wire()["reservation"]
    assert snap["booking_id"] == "BK12345" and snap["restaurant_name"] == "Ivoryy Fusion Bar"
    assert snap["status"] == "CONFIRMED" and snap["final_amount"] == 500.0
    # party_size / slot_time came from the call args (server didn't echo them).
    assert snap["party_size"] == 4 and snap["slot_time"] == "20:00"
    assert provider.merge({}, result)["session_reservation"]["booking_id"] == "BK12345"


def test_dining_cards_capped_and_deduped():
    cards = DistrictProvider().build_attachments(_result("dining"))
    dining = cards[0].to_wire()["dining_restaurants"]
    ids = [r["res_id"] for r in dining]
    assert len(ids) == len(set(ids)) and len(ids) <= 8


def test_district_resolver_learns_and_chains_movie_id():
    # Regression: "showtimes for The Odyssey" plans search_movies -> search_shows
    # (__MOVIE_ID__); without District aliases nothing was ever learned and the
    # literal "__MOVIE_ID__" string reached the server.
    r = DistrictProvider.resolver
    values: dict[str, str] = {}
    learned = r.learn(values, {"data": {"movies": [{"movie_id": "MV42", "title": "The Odyssey"}]}})
    assert values["__MOVIE_ID__"] == "MV42" and learned["__MOVIE_ID__"] == "MV42"
    out = r.substitute({"filters": [{"filter_name": "movie_id", "filter_values": ["__MOVIE_ID__"]}]}, values)
    assert out["filters"][0]["filter_values"] == ["MV42"]


def test_district_resolver_search_reissues_stale_ids():
    r = DistrictProvider.resolver
    values = {"__MOVIE_ID__": "stale", "__RES_ID__": "keep"}
    r.clear_stale(values, "search_movies")
    assert "__MOVIE_ID__" not in values and values["__RES_ID__"] == "keep"


def test_unresolved_flags_any_leftover_placeholder_token():
    # Alias-independent: ids the resolver can't learn must still block the call.
    r = PlaceholderResolver()
    args = {"filters": [{"filter_name": "movie_id", "filter_values": ["__MOVIE_ID__"]}]}
    assert r.unresolved(args) == ["__MOVIE_ID__"]
    assert r.unresolved({"location": {"latitude": "__USER_LAT__"}}) == ["__USER_LAT__"]
    assert r.unresolved({"ok": "resolved", "n": 3}) == []


def test_normalize_args_renests_stray_keys_into_filters():
    # Regression: the planner emitted movie_name / movie_id / show_date at top
    # level; the server rejects unknown top-level keys on every search tool.
    p = DistrictProvider()
    out = p.normalize_args(
        "search_movies",
        {"location": {"latitude": 1.0, "longitude": 2.0}, "movie_name": "The Odyssey"},
    )
    assert out == {
        "location": {"latitude": 1.0, "longitude": 2.0},
        "filters": [{"filter_name": "movie_name", "filter_values": ["The Odyssey"]}],
    }
    out = p.normalize_args(
        "search_shows",
        {
            "location": {"latitude": 1.0, "longitude": 2.0},
            "movie_id": "MV42",
            "show_date": "2026-06-10",
            "filters": [{"filter_name": "language", "filter_values": ["Hindi"]}],
        },
    )
    assert out["filters"] == [
        {"filter_name": "language", "filter_values": ["Hindi"]},
        {"filter_name": "movie_id", "filter_values": ["MV42"]},
        {"filter_name": "show_date", "filter_values": ["2026-06-10"]},
    ]
    clean = {"location": {"latitude": 1.0, "longitude": 2.0}, "filters": []}
    assert p.normalize_args("search_movies", dict(clean)) == clean
    assert p.normalize_args("get_booking_details", {"booking_id": "B1"}) == {"booking_id": "B1"}
