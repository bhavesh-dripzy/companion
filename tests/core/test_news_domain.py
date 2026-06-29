"""News domain (newsdata.io) — extractor, category params, list/explain, and ref resolution."""

from __future__ import annotations

import asyncio

from core.agent.domains import news_agent
from core.agent.providers.newsdata.client import NewsBudgetExceeded
from core.agent.providers.newsdata.news import extract_news, news_params
from core.agent.result_memory import remember

_PAYLOAD = {
    "results": [
        {
            "title": "Deepti Sharma scripts history",
            "description": "Deepti Sharma became the highest wicket-taker in women's T20Is.",
            "source_name": "The Hawk",
            "pubDate": "2026-06-28 16:34:44",
            "link": "http://a",
            "image_url": "http://img",
        },
        *[{"title": f"x{i}", "description": f"d{i}", "link": f"http://{i}"} for i in range(20)],
    ]
}


# ── params + extractor ──────────────────────────────────────────────────────
def test_news_params_category_and_query():
    assert news_params() == {"country": "in", "language": "en"}
    assert news_params(category="sports") == {
        "country": "in",
        "language": "en",
        "category": "sports",
    }
    assert news_params("cricket", "sports") == {
        "country": "in",
        "language": "en",
        "category": "sports",
        "q": "cricket",
    }
    # unknown category is ignored (not sent as a bad param)
    assert "category" not in news_params(category="cricket")


def test_extract_news_caps_at_10_and_keeps_description():
    rows = extract_news(_PAYLOAD)
    assert len(rows) == 10  # never more than 10
    assert "highest wicket-taker" in rows[0]["description"]
    assert rows[0]["source"] == "The Hawk"


# ── handler ─────────────────────────────────────────────────────────────────
class _State:
    user_id = "u1"

    def __init__(self):
        self.serp_session: dict = {}

    def recent_history(self):
        return []


def _brain(mode="list", category="", query="", ref="", ack="dekh raha…"):
    async def _f(_ctx):
        return {"mode": mode, "category": category, "query": query, "ref": ref, "ack": ack}

    return _f


def test_handle_list_uses_category_param(monkeypatch):
    seen = {}

    async def fake_search(params):
        seen.update(params)
        return _PAYLOAD

    monkeypatch.setattr(news_agent, "news_brain", _brain(mode="list", category="sports"))
    monkeypatch.setattr(news_agent, "search", fake_search)
    out = asyncio.run(news_agent.handle(_State(), {"message": "sports ki news"}))
    assert seen.get("category") == "sports"  # category param actually used
    att = out["attachments"][0]
    assert att["kind"] == "news_cards" and len(att["articles"]) == 10
    assert "khabrein" in out["say"]


def test_handle_explain_fresh_topic(monkeypatch):
    async def fake_search(_p):
        return _PAYLOAD

    async def fake_response(ctx):
        assert ctx["articles"][0]["description"]
        return "Deepti ne record banaya — short mein yahi hai."

    monkeypatch.setattr(news_agent, "news_brain", _brain(mode="explain", query="Deepti Sharma"))
    monkeypatch.setattr(news_agent, "search", fake_search)
    monkeypatch.setattr(news_agent, "news_response", fake_response)
    out = asyncio.run(news_agent.handle(_State(), {"message": "explain Deepti news"}))
    assert out["say"] == "Deepti ne record banaya — short mein yahi hai."
    assert out["attachments"][0]["kind"] == "news_cards"


def test_handle_explain_by_ref_pins_exact_article_without_refetch(monkeypatch):
    state = _State()
    a1 = {"title": "First news", "description": "d1", "link": "http://1"}
    a2 = {"title": "Second news", "description": "Ben Stokes retires.", "link": "http://2"}
    remember(state, "news", [a1, a2], lambda a: a["title"])  # n1, n2 already shown

    async def must_not_fetch(_p):
        raise AssertionError("ref-explain must not re-fetch")

    async def fake_response(ctx):
        # narrator gets ONLY the pinned article
        assert len(ctx["articles"]) == 1 and ctx["articles"][0]["title"] == "Second news"
        return "Ben Stokes ne retirement le liya."

    monkeypatch.setattr(news_agent, "news_brain", _brain(mode="explain", ref="n2"))
    monkeypatch.setattr(news_agent, "search", must_not_fetch)
    monkeypatch.setattr(news_agent, "news_response", fake_response)
    out = asyncio.run(news_agent.handle(state, {"message": "2nd wali explain karo"}))
    att = out["attachments"][0]
    assert att["articles"] == [a2]  # exactly the card the user pointed at
    assert out["say"] == "Ben Stokes ne retirement le liya."


def test_handle_no_results(monkeypatch):
    async def fake_search(_p):
        return {"results": []}

    monkeypatch.setattr(news_agent, "news_brain", _brain(query="zzz"))
    monkeypatch.setattr(news_agent, "search", fake_search)
    out = asyncio.run(news_agent.handle(_State(), {"message": "zzz news"}))
    assert out["attachments"] == [] and "zzz" in out["say"]


def test_handle_fetch_error_graceful(monkeypatch):
    async def boom(_p):
        raise RuntimeError("NEWSDATA_API_KEY is not configured")

    monkeypatch.setattr(news_agent, "news_brain", _brain())
    monkeypatch.setattr(news_agent, "search", boom)
    out = asyncio.run(news_agent.handle(_State(), {"message": "news"}))
    assert out["attachments"] == [] and "news" in out["say"].lower()


def test_handle_budget_exceeded(monkeypatch):
    async def over(_p):
        raise NewsBudgetExceeded("quota")

    monkeypatch.setattr(news_agent, "news_brain", _brain())
    monkeypatch.setattr(news_agent, "search", over)
    out = asyncio.run(news_agent.handle(_State(), {"message": "news"}))
    assert out["attachments"] == [] and "quota" in out["say"].lower()
