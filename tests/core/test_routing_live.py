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
