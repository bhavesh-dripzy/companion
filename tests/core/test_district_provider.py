"""Golden test: the clean `DistrictProvider` builds typed cards from REAL data.

Drives the provider against sanitized real captures (dining / movies / events,
live read-only searches on admin_bhavesh), asserting each discovery surface emits
its own typed attachment + feeds the matching session LRU, reflecting the real
entities the live turns surfaced.
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.district import DistrictProvider

pytestmark = pytest.mark.component

_DIR = Path(__file__).parent / "fixtures/district"


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


def test_dining_cards_capped_and_deduped():
    cards = DistrictProvider().build_attachments(_result("dining"))
    dining = cards[0].to_wire()["dining_restaurants"]
    ids = [r["res_id"] for r in dining]
    assert len(ids) == len(set(ids)) and len(ids) <= 8
