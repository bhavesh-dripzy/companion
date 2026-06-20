"""Live prod e2e: the agent FETCHES information it shouldn't answer from memory —
news and general facts — across English / Hindi / Hinglish, instead of refusing.

Two assertion tiers (structured signals only, never prose):
  * STRUCTURAL (deterministic, never flaky): an informational turn is serp-backed,
    so it is never tool-starved — the bug that made it refuse news. Guards the fix.
  * END-TO-END (canonical queries): serp actually ran a fetch tool and returned
    news_cards / web_search_cards. Depends on the live router model + SerpApi.

Read-only. Run with COMPANION_LIVE_TOKEN (or USERNAME/PASSWORD) set; see conftest.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live

_FETCH_CARDS = {"web_search_cards", "news_cards"}

# Informational asks across languages — none should be answered from memory.
_NEWS = [
    "show me the latest news",
    "what's happening in the world right now",
    "आज की ताज़ा खबरें दिखाओ",
    "aaj ki taaza khabrein dikhao",
    "duniya mein aaj kya ho raha hai",
]
_FACTS = [
    "who is the CEO of Anthropic",
    "what is the current population of Pune",
    "भारत के वर्तमान प्रधानमंत्री कौन हैं",
    "Anthropic ka CEO kaun hai",
]


def _kinds(env: dict) -> set[str]:
    return {a.get("kind") for a in (env.get("attachments") or []) if isinstance(a, dict)}


def _serp_backed(env: dict) -> bool:
    return "serp" in (env.get("selected_tool_providers") or [])


@pytest.mark.parametrize("q", _NEWS + _FACTS)
def test_informational_turns_are_serp_backed_not_tool_starved(live_client, q):
    """The structural fix: any informational turn reaches the serp planner, so the
    model has fetch tools and can never fall back to 'I don't have real-time access'."""
    env = live_client.ask(q)
    assert env.get("status") == "ok", env.get("error")
    assert _serp_backed(env), {"q": q, "task": env.get("task_type"), "providers": env.get(
        "selected_tool_providers")}


@pytest.mark.parametrize("q", ["show me the latest news", "who is the CEO of Anthropic"])
def test_canonical_queries_fetch_and_cite(live_client, q):
    """End-to-end: the planner runs a fetch tool and the reply carries source cards."""
    env = live_client.ask(q)
    assert env.get("status") == "ok", env.get("error")
    assert _FETCH_CARDS & _kinds(env), {"q": q, "kinds": _kinds(env)}
