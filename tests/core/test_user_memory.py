"""User-memory injection: the generic render seam, the planner reuse, the narrator
injection, and message-language mirroring. No hardcoded fields or interest lists."""

import asyncio
from types import SimpleNamespace

import pytest

from core.app_factory import build_runner
from core.boundaries.null_impls import NoopPublisher
from core.contracts.profile import Profile
from core.contracts.turn import TurnRequest
from core.llm.config import ResolvedLlm
from core.middleware.user_memory import render_user_memory
from core.pipeline.stages.narrate import _reply_language
from core.registry import ProviderRegistry, ToolRegistry
from core.registry.seed import builtin_tools

pytestmark = pytest.mark.unit

_LLM = ResolvedLlm(provider="sarvam", api_key="x", model="m")

# Personalization is personal info + preferences. The leadgen fields (interests /
# leads) are present here ONLY to prove they are NOT surfaced for personalization.
_PROFILE = Profile(
    user_id="u1",
    name="Bhavesh",
    city="Gurgaon",
    language="Hindi",
    occupation="Not mentioned",  # placeholder must be skipped
    phone="9990001111",  # private — must NOT appear
    preferences=["prefers vegetarian", "follows cricket", "prefers vegetarian"],  # dup collapses
    interests=[{"category": "electronics", "weight": "0.9", "details": ["iPhone 16 Pro"]}],
    open_leads=[{"category": "electronics", "product": "1.5 ton split AC"}],
    matched_leads=[{"category": "electronics", "product": "split AC", "vendor_name": "Daikin"}],
)


def test_render_includes_identity_and_preferences_not_products():
    out = render_user_memory(_PROFILE)
    assert "Bhavesh" in out and "Gurgaon" in out and "Hindi" in out
    assert "prefers vegetarian" in out and "follows cricket" in out
    assert out.count("prefers vegetarian") == 1  # de-duped
    # products / interests / leads are leadgen (vendor matching), NOT personalization
    assert "iPhone 16 Pro" not in out
    assert "1.5 ton split AC" not in out and "Daikin" not in out and "re-pitch" not in out


def test_render_is_privacy_light_and_skips_placeholders():
    out = render_user_memory(_PROFILE)
    assert "9990001111" not in out  # phone never injected
    assert "Not mentioned" not in out  # placeholder occupation skipped


def test_render_empty_when_nothing_known():
    assert render_user_memory(Profile(user_id="u1")) == ""
    assert render_user_memory(None) == ""


def test_extractor_captures_and_normalizes_preferences():
    from companion_api.personal_fact_extractor import (
        normalize_personal_facts,
        normalize_preferences,
    )

    # list + scalar inputs, case-insensitive de-dup, whitespace trim
    assert normalize_preferences(
        {"preferences": ["Vegetarian", "vegetarian", " follows cricket "]}
    ) == ["Vegetarian", "follows cricket"]
    assert normalize_preferences({"preferences": "pays via UPI"}) == ["pays via UPI"]
    assert normalize_preferences({}) == []
    # preferences ride alongside the scalar personal columns
    out = normalize_personal_facts({"city": "Pune", "preferences": ["loves spicy food"]})
    assert out["city"] == "Pune" and out["preferences"] == ["loves spicy food"]


def test_reply_language_mirrors_message_then_profile_fallback():
    # explicit reply_language (voice) wins
    ctx = SimpleNamespace(request=SimpleNamespace(reply_language="hinglish"), profile=_PROFILE)
    assert _reply_language(ctx) == "hinglish"
    # no explicit language -> mirror the message, fall back to the stored language
    ctx = SimpleNamespace(request=SimpleNamespace(reply_language=None), profile=_PROFILE)
    out = _reply_language(ctx)
    assert "SAME language" in out and "Hindi" in out
    # no profile / placeholder -> English fallback
    ctx = SimpleNamespace(request=SimpleNamespace(reply_language=None), profile=None)
    assert "English" in _reply_language(ctx)


def _runner_with_profile(profile, capture):
    tools = ToolRegistry()
    tools.register_all(builtin_tools())
    registry = ProviderRegistry(tools=tools)

    async def complete(*, user_message, system_prompt, llm):
        if "You are Companion" in system_prompt:  # narrator
            capture["narrator_user"] = user_message
            capture["narrator_system"] = system_prompt
            return {"message": "ok"}
        return {"task_type": "normal_query", "reasoning": "general"}

    class _Profile:
        async def get(self, user_id):
            return profile

    return build_runner(
        registry=registry,
        profile_service=_Profile(),
        publisher=NoopPublisher(),
        complete_json=complete,
        geocode=None,
    )


def test_narrator_receives_memory_block_and_language_instruction():
    capture: dict = {}
    runner = _runner_with_profile(_PROFILE, capture)
    # normal_query is serp-backed; with no real serp key the planner/execute no-op,
    # but the narrator still runs and must receive the memory block.
    asyncio.run(runner.run(TurnRequest(message="hi", user_id="u1"), _LLM))
    assert "WHAT YOU KNOW ABOUT THIS USER" in capture["narrator_user"]
    assert "Bhavesh" in capture["narrator_user"]
    assert "SAME language" in capture["narrator_system"]  # mirrors message language
