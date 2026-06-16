"""BlaBlaCar Tier-1 carpool deep-link provider: place_id encoding, URL build, routing."""

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from core.contracts.tool_call import ToolCall
from core.middleware.errors import InvalidArgsError
from core.providers.base import ExecCtx
from core.providers.plugins.blablacar.provider import (
    BlaBlaCarProvider,
    _bbc_place_id,
    _compute,
    _normalize_date,
)

pytestmark = pytest.mark.unit

# The exact blob from a real blablacar.in/search URL (New Delhi).
_NEW_DELHI_PID = "ChIJLbZ-NFv9DDkRzk0gTkm3wlI"
_NEW_DELHI_BLOB = "eyJpIjoiQ2hJSkxiWi1ORnY5RERrUnprMGdUa20zd2xJIiwicCI6MSwidiI6MSwidCI6WzRdfQ=="


def _patch_geocode(monkeypatch, mapping):
    import companion_api.google_maps as gm

    monkeypatch.setattr(gm, "geocode_place_id", lambda text, region=None: mapping.get(text))


def test_place_id_matches_blablacar_encoding():
    assert _bbc_place_id(_NEW_DELHI_PID) == _NEW_DELHI_BLOB
    assert json.loads(base64.b64decode(_bbc_place_id(_NEW_DELHI_PID)))["i"] == _NEW_DELHI_PID


def test_deeplink_with_place_ids(monkeypatch):
    _patch_geocode(
        monkeypatch,
        {"New Delhi": _NEW_DELHI_PID, "Karnal": "ChIJlz7gURhwDjkRB3bM4bmoSvg"},
    )
    out = _compute({"origin": "New Delhi", "destination": "Karnal", "date": "2026-06-17", "seats": 2})
    assert out["ok"]
    split = urlsplit(out["deeplink"])
    assert split.netloc == "www.blablacar.in" and split.path == "/search"
    q = parse_qs(split.query)
    assert q["fn"] == ["New Delhi"] and q["tn"] == ["Karnal"]
    assert q["db"] == ["2026-06-17"] and q["seats"] == ["2"]
    assert q["from_place_id"] == [_NEW_DELHI_BLOB]
    assert q["to_place_id"] == [_bbc_place_id("ChIJlz7gURhwDjkRB3bM4bmoSvg")]


def test_deeplink_without_maps_falls_back_to_names(monkeypatch):
    _patch_geocode(monkeypatch, {})  # geocode returns None for everything
    out = _compute({"origin": "Delhi", "destination": "Jaipur"})
    q = parse_qs(urlsplit(out["deeplink"]).query)
    assert q["fn"] == ["Delhi"] and q["tn"] == ["Jaipur"]
    assert "from_place_id" not in q and "to_place_id" not in q
    assert q["seats"] == ["1"]  # default


def test_normalize_date_formats_and_default():
    assert _normalize_date("2026-06-17") == "2026-06-17"
    assert _normalize_date("17-06-2026") == "2026-06-17"
    assert _normalize_date("17/06/2026") == "2026-06-17"
    today = _normalize_date("")
    assert len(today) == 10 and today[4] == "-"  # YYYY-MM-DD shape


def test_missing_route_is_error():
    out = _compute({"origin": "Delhi"})
    assert not out["ok"] and out["error_code"] == "missing_route"


def test_execute_one_and_attachment(monkeypatch):
    _patch_geocode(monkeypatch, {})
    provider = BlaBlaCarProvider()
    res = asyncio.run(
        provider.run(
            [ToolCall(tool="search_carpools", arguments={"origin": "Delhi", "destination": "Agra"})],
            ExecCtx(),
        )
    )
    assert res.ok
    tr = res.results[0]
    assert tr.ok and tr.data["deeplink"].startswith("https://www.blablacar.in/search?")
    cards = provider.build_attachments(res)
    assert cards and cards[0].kind == "blablacar_card"
    wire = cards[0].to_wire()
    assert wire["url"].startswith("https://www.blablacar.in/search?")
    assert wire["origin"] == "Delhi" and wire["destination"] == "Agra"


def test_unknown_tool_errors():
    provider = BlaBlaCarProvider()
    with pytest.raises(InvalidArgsError):
        asyncio.run(provider.execute_one(ToolCall(tool="nope", arguments={}), ExecCtx()))


# ── routing + registry wiring ─────────────────────────────────────────────


def test_routing_carpool_to_blablacar():
    from core.pipeline.orchestration import parse_routing

    decision = parse_routing({"task_type": "carpool", "reasoning": "carpool: delhi->jaipur"}, None)
    assert decision.task_type == "carpool"
    assert decision.providers == ["blablacar"]


def test_registry_resolves_search_carpools_to_blablacar():
    from core.registry.bootstrap import build_registry

    reg = build_registry()
    assert reg.get("blablacar") is not None
    assert reg.tools.provider_of("search_carpools") == "blablacar"
    assert [t.name for t in reg.tools.for_provider("blablacar")] == ["search_carpools"]
