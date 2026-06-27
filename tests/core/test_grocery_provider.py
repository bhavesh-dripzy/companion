"""Provider fan-out plan (plan_intents) + narrator deterministic-fallback grounding.

No live deps. `plan_intents` is pure/deterministic: one router intent → {provider: its items}.
Untagged items fan out to every app; per-item `provider` tags route each item to its own app.
"""

from __future__ import annotations

import asyncio

import pytest

from core.agent.domains import grocery_resolve as R
from core.agent.llm.roles import narrate
from core.agent.registry import REGISTRY


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Force the LLM offline so resolution/narration take their deterministic fallback paths,
    regardless of whether a SARVAM key leaked into the env via another test's .env load."""

    async def _empty(system, payload):
        return {}

    monkeypatch.setattr("core.agent.llm.roles._complete", _empty)


PROVIDERS = REGISTRY.providers_for("grocery")
IDS = [p.id for p in PROVIDERS]


def _intent(action="add", hint=None, phrase="amul bread", item_refs=None):
    if item_refs is None:
        item_refs = [{"phrase": phrase, "qty": 1}] if phrase else []
    return {"action": action, "provider_hint": hint, "item_refs": item_refs}


# ---------------- plan_intents (deterministic provider fan-out) ----------------
def test_explicit_hint_scopes_to_one_app():
    plan = R.plan_intents(_intent(hint="zepto"), IDS)
    assert set(plan) == {"zepto"}
    assert [r["phrase"] for r in plan["zepto"]] == ["amul bread"]


def test_no_app_fans_out_to_both():
    plan = R.plan_intents(_intent(hint=None, phrase="bread"), IDS)
    assert set(plan) == set(IDS)
    assert all([r["phrase"] for r in items] == ["bread"] for items in plan.values())


def test_both_hint_fans_out():
    assert set(R.plan_intents(_intent(hint="both", phrase="sasta sooji"), IDS)) == set(IDS)


def test_search_no_app_fans_out():
    assert set(R.plan_intents(_intent(action="search", phrase="milk"), IDS)) == set(IDS)


def test_per_item_provider_routes_each_to_its_app():
    refs = [
        {"phrase": "bread", "qty": 1, "provider": "instamart"},
        {"phrase": "sooji", "qty": 1, "provider": "zepto"},
    ]
    plan = R.plan_intents(_intent(item_refs=refs), IDS)
    assert [r["phrase"] for r in plan["instamart"]] == ["bread"]
    assert [r["phrase"] for r in plan["zepto"]] == ["sooji"]


def test_same_app_items_merge_into_one_bucket():
    refs = [
        {"phrase": "doodh", "qty": 1, "provider": "instamart"},
        {"phrase": "bread", "qty": 1, "provider": "instamart"},
    ]
    plan = R.plan_intents(_intent(item_refs=refs), IDS)
    assert set(plan) == {"instamart"}
    assert [r["phrase"] for r in plan["instamart"]] == ["doodh", "bread"]


def test_itemless_action_fans_out_empty():
    plan = R.plan_intents(_intent(action="clear", hint=None, phrase=""), IDS)
    assert set(plan) == set(IDS) and all(items == [] for items in plan.values())


def test_itemless_action_with_hint_one_app():
    plan = R.plan_intents(_intent(action="show_cart", hint="zepto", phrase=""), IDS)
    assert set(plan) == {"zepto"} and plan["zepto"] == []


def test_unknown_provider_tag_falls_back_to_both():
    # a bogus per-item provider (not a real app) → treated as untagged → fans out to both
    refs = [{"phrase": "bread", "qty": 1, "provider": "blinkit"}]
    assert set(R.plan_intents(_intent(item_refs=refs), IDS)) == set(IDS)


# ---------------- narrate fallback never lies (LLM unavailable in tests) ----------------
def test_narrate_need_provider_uses_note_not_hogaya():
    msg = asyncio.run(
        narrate({"status": "need_provider", "note": "Kaunse app — Instamart ya Zepto?"})
    )
    assert msg == "Kaunse app — Instamart ya Zepto?"
    assert msg != "Ho gaya."


def test_narrate_ambiguous_asks_not_success():
    msg = asyncio.run(narrate({"status": "ambiguous"}))
    assert "Instamart" in msg and "Zepto" in msg and msg != "Ho gaya."


def test_narrate_error_does_not_fake_success():
    msg = asyncio.run(
        narrate({"status": "error", "error": "max_qty", "error_hint": "Max limit hai."})
    )
    assert msg == "Max limit hai." and msg != "Ho gaya."
