"""Provider selection/inference (§6b) + narrator deterministic-fallback grounding.

No live deps: the LLM resolver is unavailable in tests, so resolution falls back to the
deterministic name-overlap matcher — which is exactly the path we assert here. The LLM-only
behaviour (price-qualified "120 waala amul taaza") is covered by the live e2e.
"""

from __future__ import annotations

import asyncio

import pytest

from core.agent.contracts import Product
from core.agent.domains import grocery_resolve as R
from core.agent.llm.roles import narrate
from core.agent.registry import REGISTRY
from core.agent.state import ConversationState


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    """Force the LLM offline so resolution/narration take their deterministic fallback paths,
    regardless of whether a SARVAM key leaked into the env via another test's .env load."""

    async def _empty(system, payload):
        return {}

    monkeypatch.setattr("core.agent.llm.roles._complete", _empty)


def _state_with(*products: Product) -> ConversationState:
    st = ConversationState(user_id="u", chat_id="c")
    st.catalog.append(list(products))
    return st


def _p(provider, name, rupees, brand=""):
    return Product(
        provider=provider,
        item_id=f"{provider}:{name}",
        name=name,
        price_minor=int(rupees * 100),
        brand=brand,
    )


PROVIDERS = REGISTRY.providers_for("grocery")
IDS = {p.id for p in PROVIDERS}


def _intent(action="add", hint=None, phrase="amul bread"):
    return {
        "action": action,
        "provider_hint": hint,
        "item_refs": [{"phrase": phrase, "qty": 1}] if phrase else [],
    }


# ---------------- choose_providers (§6b ladder) ----------------
def test_explicit_hint_wins():
    st = _state_with(_p("instamart", "Amul Bread", 40))
    chosen, ask = asyncio.run(R.choose_providers(st, _intent(hint="zepto"), PROVIDERS))
    assert ask is None and chosen == ["zepto"] and st.active_provider == "zepto"


def test_search_fans_out():
    st = _state_with()
    chosen, ask = asyncio.run(R.choose_providers(st, _intent(action="search"), PROVIDERS))
    assert ask is None and set(chosen) == IDS


def test_fanout_sets_active_both():
    st = _state_with()
    asyncio.run(R.choose_providers(st, _intent(action="search"), PROVIDERS))
    assert st.active_provider == "both"  # both apps active, not a single sticky one


def test_after_fanout_cart_op_infers_not_sticky():
    # fan-out leaves active="both"; an add for an item that lives only on instamart must infer it,
    # NOT get stuck on a stale single sticky app.
    st = _state_with(
        _p("instamart", "Amul Taaza Milky Milk", 30, "Amul"),
        _p("zepto", "Country Delight Milk", 46, ""),
    )
    st.active_provider = "both"
    chosen, ask = asyncio.run(
        R.choose_providers(st, _intent(phrase="amul taaza milky milk"), PROVIDERS)
    )
    assert ask is None and chosen == ["instamart"]


def test_sticky_active_provider():
    st = _state_with()
    st.active_provider = "zepto"
    chosen, ask = asyncio.run(R.choose_providers(st, _intent(), PROVIDERS))
    assert ask is None and chosen == ["zepto"]


def test_infer_from_item_unique_provider():
    # "amul bread" exists only on instamart → inferred, no ask
    st = _state_with(
        _p("instamart", "Amul Bread", 40, "Amul"),
        _p("zepto", "Mother Dairy Milk", 30, "Mother Dairy"),
    )
    chosen, ask = asyncio.run(R.choose_providers(st, _intent(phrase="amul bread"), PROVIDERS))
    assert ask is None and chosen == ["instamart"]


def test_true_tie_asks_with_note():
    # "amul taaza" in BOTH → infer returns None → clean need_provider ask carrying a note
    st = _state_with(
        _p("instamart", "Amul Taaza Milk", 120, "Amul"), _p("zepto", "Amul Taaza Toned", 30, "Amul")
    )
    chosen, ask = asyncio.run(R.choose_providers(st, _intent(phrase="amul taaza"), PROVIDERS))
    assert chosen == [] and ask is not None
    assert ask["status"] == "need_provider" and ask["note"]
    assert "instamart" in ask["note"].lower() and "zepto" in ask["note"].lower()


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
