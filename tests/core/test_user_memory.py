"""User memory — the capture → store → recall loop.

Covers: the extractor's normalization (new columns: family_names, office/home
locations, preferences), the privacy-light prompt render, the preference union-merge,
and the fail-open capture path with the LLM + Dynamo seams stubbed out.
"""

from __future__ import annotations

import asyncio

from companion_api.personal_fact_extractor import (
    extract_personal_facts,
    normalize_personal_facts,
    normalize_preferences,
)
from companion_api.profile_cache import ProfileCacheRepository
from core.agent import memory

# ── render ────────────────────────────────────────────────────────────────────────

_PROFILE = {
    "name": "Bhavesh",
    "city": "Gurgaon",
    "language": "Hindi",
    "occupation": "Not mentioned",  # placeholder must be skipped
    "office_location": "Cyber City Tower B, Gurugram",
    "home_location": "Sector 45",
    "family_names": "wife Priya, son Aarav",
    "age": "32",  # stored but never rendered
    "phone": "9990001111",  # private — must NOT appear
    "email": "b@x.com",  # private — must NOT appear
    "preferences": ["prefers vegetarian", "follows cricket", "Prefers Vegetarian"],
}


def test_render_includes_identity_locations_and_preferences():
    out = memory.render_user_memory(_PROFILE)
    assert "Bhavesh" in out and "Gurgaon" in out and "Hindi" in out
    assert "Cyber City Tower B" in out and "Sector 45" in out
    assert "wife Priya, son Aarav" in out
    assert "prefers vegetarian" in out and "follows cricket" in out
    assert out.count("vegetarian") == 1  # case-insensitively de-duped


def test_render_is_privacy_light_and_skips_placeholders():
    out = memory.render_user_memory(_PROFILE)
    assert "9990001111" not in out and "b@x.com" not in out and "32" not in out
    assert "Not mentioned" not in out


def test_render_empty_when_nothing_known():
    assert memory.render_user_memory({}) == ""
    assert memory.render_user_memory(None) == ""
    assert memory.render_user_memory({"occupation": "unknown"}) == ""


# ── extractor normalization ──────────────────────────────────────────────────────


def test_normalize_keeps_new_columns_and_coerces_lists():
    out = normalize_personal_facts(
        {
            "occupation": "doctor",
            "office_location": "Cyber City, Gurugram",
            "home_location": " Sector 45 ",
            "family_names": ["wife Priya", "son Aarav"],
            "not_a_column": "dropped",
            "city": "",
        }
    )
    assert out == {
        "occupation": "doctor",
        "office_location": "Cyber City, Gurugram",
        "home_location": "Sector 45",
        "family_names": "wife Priya, son Aarav",
    }


def test_normalize_preferences_dedupes_and_caps():
    prefs = normalize_preferences(
        {"preferences": ["vegetarian", "Vegetarian", "", "follows cricket"]}
    )
    assert prefs == ["vegetarian", "follows cricket"]
    assert normalize_preferences({"preferences": "pays via UPI"}) == ["pays via UPI"]
    assert normalize_preferences({}) == []
    many = normalize_preferences({"preferences": [f"p{i}" for i in range(50)]})
    assert len(many) == 20


def test_extract_merges_facts_and_preferences_via_stub_llm():
    async def stub_complete(**_kw):
        return {
            "occupation": "engineer",
            "preferences": ["vegetarian"],
            "hallucinated": "x",
        }

    out = asyncio.run(
        extract_personal_facts(
            user_message="main engineer hoon, veg khata hoon",
            assistant_message="",
            complete_json=stub_complete,
            llm=object(),
        )
    )
    assert out == {"occupation": "engineer", "preferences": ["vegetarian"]}


def test_extract_fails_open():
    async def broken(**_kw):
        raise RuntimeError("llm down")

    out = asyncio.run(
        extract_personal_facts(
            user_message="hello", assistant_message="", complete_json=broken, llm=object()
        )
    )
    assert out == {}


# ── preference union-merge (repo) ────────────────────────────────────────────────


def _stub_repo(existing: list[str]):
    repo = ProfileCacheRepository.__new__(ProfileCacheRepository)
    written: dict = {}
    repo.get = lambda uid: {"preferences": existing}
    repo.update_fields = lambda uid, fields: written.update(fields)
    return repo, written


def test_merge_preferences_unions_dedupes_and_caps():
    repo, written = _stub_repo(["vegetarian", "follows cricket"])
    repo.merge_preferences("u1", ["Vegetarian", "pays via UPI"])
    assert written["preferences"] == ["vegetarian", "follows cricket", "pays via UPI"]

    repo, written = _stub_repo([f"p{i}" for i in range(20)])
    repo.merge_preferences("u1", ["new one"], cap=20)
    assert len(written["preferences"]) == 20
    assert written["preferences"][-1] == "new one"

    repo, written = _stub_repo(["vegetarian"])
    repo.merge_preferences("u1", ["  ", ""])
    assert written == {}  # nothing new → no write


# ── capture gate + fail-open ─────────────────────────────────────────────────────


def test_worth_capturing_gate():
    assert not memory.worth_capturing("")
    assert not memory.worth_capturing("haan")
    assert not memory.worth_capturing("2 kar do")
    assert memory.worth_capturing("main Gurgaon mein rehta hoon")


def test_capture_persists_extracted_facts(monkeypatch):
    import core.llm.config as llm_config

    monkeypatch.setattr(llm_config, "resolve_llm_from_env", lambda: (object(), ""))

    import companion_api.personal_fact_extractor as pfe

    async def fake_extract(**_kw):
        return {"occupation": "doctor", "preferences": ["vegetarian"]}

    monkeypatch.setattr(pfe, "extract_personal_facts", fake_extract)

    persisted: dict = {}
    monkeypatch.setattr(
        memory, "_persist_facts", lambda uid, facts: persisted.update({uid: facts})
    )
    asyncio.run(memory.capture("u1", "main doctor hoon aur vegetarian hoon"))
    assert persisted == {"u1": {"occupation": "doctor", "preferences": ["vegetarian"]}}


def test_capture_never_raises(monkeypatch):
    import core.llm.config as llm_config

    def boom():
        raise RuntimeError("no env")

    monkeypatch.setattr(llm_config, "resolve_llm_from_env", boom)
    asyncio.run(memory.capture("u1", "main doctor hoon aur vegetarian hoon"))  # no raise


def test_recall_fails_open_without_aws(monkeypatch):
    import companion_api.profile_cache as pc

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("no dynamo")

    monkeypatch.setattr(pc, "ProfileCacheRepository", Broken)
    assert asyncio.run(memory.recall("u1")) == ""
