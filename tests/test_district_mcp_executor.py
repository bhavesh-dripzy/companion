"""Unit tests for District MCP executor helpers."""

from __future__ import annotations

from app.services.district.mcp_executor import (
    _collect_movies,
    _collect_restaurants,
    _collect_theatres,
    _geocode_location_text,
    _normalize_dining_filters,
    _normalize_movie_filters,
    _parse_envelope_data,
    _prepare_tool_args,
    _unresolved_district_placeholders,
)


def test_parse_envelope_json_string():
    env = {"data": '{"status": {"status": "success"}, "restaurants": []}'}
    assert isinstance(_parse_envelope_data(env), dict)


def test_collect_restaurants_from_envelope():
    env = {
        "is_error": False,
        "data": {
            "restaurants": [
                {"res_id": 53211, "res_name": "Chianti", "rating": 4.8},
            ]
        },
    }
    rows = _collect_restaurants(env)
    assert rows == [{"res_id": "53211", "name": "Chianti", "rating": 4.8, "cuisine": None}]


def test_collect_movies_and_theatres():
    movies_env = {"data": {"movies": [{"movie_id": "170361", "movie_name": "Raja Shivaji"}]}}
    theatres_env = {"data": {"theatres": [{"theatre_id": "1025975", "theatre_name": "INOX"}]}}
    assert _collect_movies(movies_env)[0]["movie_id"] == "170361"
    assert _collect_theatres(theatres_env)[0]["theatre_id"] == "1025975"


def test_normalize_filters():
    dining = _normalize_dining_filters([{"name": "cuisine_type", "values": ["Italian"]}])
    movie = _normalize_movie_filters([{"filter_name": "language", "filter_values": ["Hindi"]}])
    assert dining == [{"name": "cuisine_type", "values": ["Italian"]}]
    assert movie == [{"filter_name": "language", "filter_values": ["Hindi"]}]


def test_geocode_location_text():
    coords = _geocode_location_text("restaurants near Koramangala")
    assert coords is not None
    assert abs(coords[0] - 12.9352) < 0.01


def test_prepare_tool_args_injects_location():
    args = _prepare_tool_args(
        "search_movies",
        {"filters": [{"filter_name": "language", "filter_values": ["Hindi"]}]},
        coords=(12.9, 77.6),
        placeholder_values={},
    )
    assert args is not None
    assert args["location"]["latitude"] == 12.9


def test_unresolved_district_placeholders():
    found = _unresolved_district_placeholders({"movie_id": "__MOVIE_ID__"}, {})
    assert found == {"__MOVIE_ID__"}
