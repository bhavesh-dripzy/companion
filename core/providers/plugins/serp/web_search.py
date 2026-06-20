"""General Google web search (SerpApi `google` engine): the catch-all lookup
the agent reaches for when a question needs a verifiable external fact that no
specific engine (flights/hotels/products/news/finance) covers. Returns a direct
answer (answer box), an entity snapshot (knowledge graph), and the top organic
results as citations — extracted lean so it fits the narrator and a card."""

from __future__ import annotations

from typing import Any

from core.providers.plugins.serp.more_engines import _clean, _trim

_MAX_RESULTS = 6


def web_search_params(args: dict[str, Any]) -> dict[str, Any]:
    return {"engine": "google", "q": args.get("query"), "gl": "in", "hl": "en", "num": 10}


def _answer(payload: dict[str, Any]) -> str | None:
    """The direct answer from the answer box, whichever shape it took."""
    box = payload.get("answer_box") if isinstance(payload.get("answer_box"), dict) else {}
    for key in ("answer", "result", "snippet"):
        value = box.get(key)
        if isinstance(value, str) and value.strip():
            return _trim(value, 320)
    snippets = box.get("snippet_highlighted_words") or box.get("list")
    if isinstance(snippets, list) and snippets:
        joined = "; ".join(str(s) for s in snippets if isinstance(s, str))
        return _trim(joined, 320) or None
    return None


def _knowledge(payload: dict[str, Any]) -> dict[str, Any] | None:
    kg = payload.get("knowledge_graph") if isinstance(payload.get("knowledge_graph"), dict) else {}
    if not kg:
        return None
    snapshot = _clean(
        {
            "title": kg.get("title"),
            "type": kg.get("type"),
            "description": _trim(kg.get("description"), 320) or None,
        }
    )
    return snapshot or None


def extract_web_search(payload: dict[str, Any]) -> dict[str, Any]:
    results = []
    for item in (payload.get("organic_results") or [])[:_MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        results.append(
            _clean(
                {
                    "title": _trim(item.get("title"), 140),
                    "snippet": _trim(item.get("snippet"), 240) or None,
                    "link": item.get("link"),
                    "source": source.get("name") if isinstance(source, dict) else source,
                    "date": item.get("date"),
                }
            )
        )
    return _clean(
        {
            "answer": _answer(payload),
            "knowledge": _knowledge(payload),
            "results": results,
        }
    )
