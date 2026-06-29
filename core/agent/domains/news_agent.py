"""News domain — newsdata.io (read-only). Two modes + exact-reference resolution:

  LIST    (router + news_brain)            → up to 10 headlines as cards (title + description)
  EXPLAIN (router + news_brain + response) → a grounded explanation from the descriptions, in the
                                             depth the user asked for; cards still shown

References ("the 2nd one", "Ben Stokes wali", "that news") are pinned via the shared result-memory
(grocery-style refs), so the explanation always matches the exact card the user pointed at — no
fuzzy re-fetch, no drift. Code owns the facts; the list line is a template, the explanation is
grounded ONLY in descriptions.
"""

from __future__ import annotations

from typing import Any

from core.agent.debug import log, short
from core.agent.events import Emit, cards, noop, say
from core.agent.llm import news_brain, news_response
from core.agent.providers.newsdata.client import NewsBudgetExceeded, search
from core.agent.providers.newsdata.news import extract_news, news_params
from core.agent.result_memory import by_ref, notepad, remember

DOMAIN = "news"


def _result(say_text: str, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "domain": DOMAIN,
        "status": "ok",
        "error": None,
        "cards": [],
        "attachments": attachments or [],
        "say": say_text,
    }


def _card(articles: list[dict[str, Any]], keyword: str | None) -> dict[str, Any]:
    return {
        "kind": "news_cards",
        "provider": "serp",
        "source": "news.latest",
        "keyword": keyword or None,
        "articles": articles,
    }


def _for_explain(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": a.get("title"),
            "description": a.get("description"),
            "source": a.get("source"),
            "date": a.get("date"),
        }
        for a in articles
    ]


def _list_line(topic: str, n: int) -> str:
    label = topic or "aaj ki top"
    return f"Yeh rahi {label} ki {n} taaza khabrein 👆 — kisi ko explain karwana ho toh batao."


async def handle(state, intent: dict[str, Any], emit: Emit = noop) -> dict[str, Any]:
    message = intent.get("message", "")
    brain = await news_brain(
        {
            "message": message,
            "history": state.recent_history(),
            "shown_news": notepad(state, DOMAIN),
        }
    )
    log.debug(
        "NEWS mode=%s cat=%r q=%r ref=%r",
        brain["mode"],
        brain["category"],
        short(brain["query"]),
        brain["ref"],
    )
    if brain["ack"]:
        await emit(say(brain["ack"]))

    # ── EXPLAIN a specific already-shown article (pinned by ref) — cards stay, no re-fetch ──
    if brain["mode"] == "explain" and brain["ref"]:
        item = by_ref(state, DOMAIN, brain["ref"])
        if item:
            attachment = _card([item], item.get("title"))
            await emit(cards([attachment], provider="serp"))
            text = await news_response({"message": message, "articles": _for_explain([item])})
            return _result(text, [attachment])
        # ref went stale → fall through to a fresh fetch

    # ── FETCH (a list, or explain of a fresh topic) ──
    try:
        articles = extract_news(await search(news_params(brain["query"], brain["category"])))
    except NewsBudgetExceeded:
        return _result("Aaj ka news quota khatam ho gaya — thodi der baad try karo.")
    except Exception as e:  # never leak raw provider/key errors
        log.debug("NEWS fetch failed: %s", e)
        return _result("Abhi news laana mushkil ho raha hai — thodi der baad try karo.")

    topic = brain["category"] or brain["query"]
    if not articles:
        return _result(f"'{topic or 'abhi'}' pe koi taaza khabar nahi mili. Koi aur topic batao?")

    remember(state, DOMAIN, articles, lambda a: a.get("title") or "")  # refs for follow-ups
    attachment = _card(articles, topic)
    await emit(cards([attachment], provider="serp"))

    if brain["mode"] == "explain":
        text = await news_response({"message": message, "articles": _for_explain(articles)})
        return _result(text, [attachment])
    return _result(_list_line(topic, len(articles)), [attachment])
