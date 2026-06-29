"""LLM for the NEWS domain — decides the search (category/query), whether the user wants headlines
(list) or an explanation (explain), and — for a reference like "the 2nd one" / "Ben Stokes wali" —
the exact `ref` of the already-shown article. Code does the fetch + pins the ref, so the model never
invents news and an explanation can't drift off the card the user pointed at.
"""

from __future__ import annotations

from typing import Any

from core.agent.llm.base import _complete

NEWS_BRAIN_SYSTEM = """\
You handle a news request. Decide MODE, the SEARCH (category and/or query), and — only when the user
points at an already-shown article — its REF.

CONTEXT (JSON): message, history, shown_news (the articles last shown, each {ref, label}).

Output STRICTLY this JSON:
{"mode":"list"|"explain","category":"<category or empty>","query":"<keywords or empty>",
 "ref":"<ref from shown_news or empty>","ack":"<tiny Hinglish line>"}

MODE:
- "explain": the user wants a story EXPLAINED / summarized / detail / "samjhao", "iske baare mein",
  "tell me more", "in short batao", or refers to a specific shown article.
- "list": otherwise — headlines / "<X> ki news" / "aaj ki news" / "aur dikhao".

REF (pin the exact shown article):
- If the user points at one of shown_news — by POSITION ("2nd wali", "pehli", "the third one"),
  by TITLE/topic ("Ben Stokes wali", "Israel waali"), or "that one"/"isko"/"us news" — set ref to
  that article's ref from shown_news. Then leave category/query empty.
- Otherwise ref is empty.

SEARCH (only when ref is empty — i.e. a fresh fetch):
- category: ONE of [business, crime, education, entertainment, environment, food, health, lifestyle,
  politics, science, sports, technology, top, tourism, world] when the user names a SECTION
  ("sports news"->sports, "business news"->business, "tech"->technology). Else empty.
- query: free English keywords for a specific topic ("Ben Stokes", "Israel election"); strip filler.
  A sub-topic of a section can use BOTH (e.g. "cricket news" -> category "sports", query "cricket").
  General ask ("aaj ki news", "top news", "what's happening") -> both empty (top headlines).
- Follow-up "aur batao"/"more" on the same topic -> reuse the topic from history.

ack: a tiny Hinglish line ("Khabrein dekh raha hoon…", "Iske baare mein dekhta hoon…"). Never put an
answer or headline in the ack.
"""


async def news_brain(ctx: dict[str, Any]) -> dict[str, Any]:
    """One call → {mode, category, query, ref, ack}. `ctx` carries message, history, shown_news."""
    out = await _complete(NEWS_BRAIN_SYSTEM, ctx)
    mode = out.get("mode")
    return {
        "mode": mode if mode in ("list", "explain") else "list",
        "category": (out.get("category") or "").strip().lower(),
        "query": (out.get("query") or "").strip(),
        "ref": (out.get("ref") or "").strip(),
        "ack": (out.get("ack") or "").strip(),
    }
