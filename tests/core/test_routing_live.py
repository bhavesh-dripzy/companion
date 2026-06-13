"""Live routing evals — hit the real orchestrator LLM to verify intent
classification. Skipped without an API key (CI / no-secret runs).

Guards the news/video false-refusal regression (prod chat 613cca04, 2026-06-12):
"What's latest in geopolitics?" and "Suggest some nice geopolitics indian
videos" misrouted to normal_query and produced "I don't have news access"
refusals. These assert the exact failing phrasings now route to data_lookup,
plus controls that timeless knowledge stays normal_query.

Run: `pytest tests/core/test_routing_live.py -m live` (needs GEMINI_API_KEY /
DEEPSEEK_API_KEY / OPENAI_API_KEY, or APP_SECRET_ID pointing at the app secret).
"""

from __future__ import annotations

import asyncio

import pytest

from core.llm.client import complete_json_object
from core.llm.config import resolve_llm_from_env
from core.pipeline.orchestration import frame_message, parse_routing
from core.pipeline.stages.route import _orchestrator_prompt

pytestmark = pytest.mark.live

_LLM, _ = resolve_llm_from_env()
_SKIP = pytest.mark.skipif(_LLM is None, reason="no LLM API key configured")


def _classify(message: str, *, history: list[dict] | None = None) -> str:
    """Run the real Route-stage classification and return the task_type."""
    previous = None
    framed = frame_message(message, previous)
    raw = asyncio.run(
        complete_json_object(
            user_message=framed, system_prompt=_orchestrator_prompt(), llm=_LLM
        )
    )
    return parse_routing(raw, previous).task_type


# The exact phrasings that refused in prod, plus sibling phrasings that share
# the same failure mode (current-info question without the literal word "news",
# video request without the literal word "YouTube").
_DATA_LOOKUP_CASES = [
    "What's latest in geopolitics?",
    "what's the latest on the middle east",
    "what's happening in indian politics",
    "any updates on the election",
    "Suggest some nice geopolitics indian videos",
    "show me some videos on the indian economy",
    "recommend videos about isro",
    "latest cricket news",
    "what is latest in the news",
]

# Controls: timeless knowledge / chit-chat must stay normal_query, so the
# broadened cues don't swallow genuine general-knowledge questions.
_NORMAL_QUERY_CASES = [
    "what is photosynthesis?",
    "explain how inflation works",
    "hello",
    "thanks!",
]


@_SKIP
@pytest.mark.parametrize("message", _DATA_LOOKUP_CASES)
def test_current_info_and_video_route_to_data_lookup(message):
    assert _classify(message) == "data_lookup", message


@_SKIP
@pytest.mark.parametrize("message", _NORMAL_QUERY_CASES)
def test_timeless_knowledge_stays_normal_query(message):
    assert _classify(message) == "normal_query", message


# ── location continuity (prod chats 5c22086f / 613cca04) ──────────────────────


def _district_plan(history: list[dict], message: str) -> dict:
    """Run the real District planner over history+message; return its constraints."""
    from core.middleware.planner_prompt import build_planner_prompt
    from core.pipeline.clock import clock_line
    from core.providers.plugins.district import DistrictProvider
    from core.registry.provider_registry import ProviderRegistry
    from core.registry.seed import builtin_tools
    from core.registry.tool_registry import ToolRegistry

    tools = ToolRegistry()
    tools.register_all(builtin_tools())
    reg = ProviderRegistry(tools=tools)
    provider = DistrictProvider()
    reg.register(provider)
    system = build_planner_prompt(provider.planner_preamble, tools.for_provider("district"))

    # Mirror Plan stage `_format_input`: clock, history, then the current message.
    lines = [clock_line()]
    for turn in history:
        lines.append(f"{turn['role']}: {turn['content']}")
    lines.append(f"user: {message}")
    raw = asyncio.run(
        complete_json_object(user_message="\n".join(lines), system_prompt=system, llm=_LLM)
    )
    return raw.get("constraints") or {}


@_SKIP
def test_named_location_carries_forward_to_near_me():
    """'movies in delhi' then 'showtimes near me' must reuse Delhi, not go null."""
    history = [
        {"role": "user", "content": "Show me movies in delhi"},
        {"role": "assistant", "content": "Here are movies playing in Delhi."},
    ]
    constraints = _district_plan(history, "Show me today's showtimes for Zootopia 2 near me")
    loc = str(constraints.get("requested_location_text") or "")
    assert "delhi" in loc.lower(), f"expected Delhi carried forward, got {loc!r}"


@_SKIP
def test_near_me_with_no_prior_place_stays_null():
    """Control: no place ever named → leave it null so ambient GPS is the fallback."""
    constraints = _district_plan([], "what movies are showing near me")
    loc = constraints.get("requested_location_text")
    assert not loc, f"expected null/empty (GPS fallback), got {loc!r}"
