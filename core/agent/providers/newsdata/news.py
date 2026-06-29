"""newsdata.io `/latest` params + lean extractor. Pure functions.

Defaults to India + English (matching the old SerpApi gl=in/hl=en). Each row keeps the
`description` (the summary newsdata gives for free) so cards are richer and a story can be
explained without a second fetch.
"""

from __future__ import annotations

from typing import Any

_MAX_ROWS = 10  # never show more than 10 at a time

# newsdata.io's fixed category set — a category request (sports/business/…) is far cleaner than a
# full-text `q` for the same word (q=sports leaks unrelated articles; category=sports does not).
CATEGORIES = {
    "business",
    "crime",
    "domestic",
    "education",
    "entertainment",
    "environment",
    "food",
    "health",
    "lifestyle",
    "other",
    "politics",
    "science",
    "sports",
    "technology",
    "top",
    "tourism",
    "world",
}


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if v not in (None, "", [], {})}


def _trim(text: Any, cap: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= cap else value[: cap - 1] + "…"


def news_params(query: str = "", category: str = "") -> dict[str, Any]:
    """India-English latest news. Use `category` for a section (sports/business/…) — much cleaner
    than `q` for those — and `q` for a free topic. Both can combine (category=sports & q=cricket)."""
    params: dict[str, Any] = {"country": "in", "language": "en"}
    cat = (category or "").strip().lower()
    if cat in CATEGORIES:
        params["category"] = cat
    q = (query or "").strip()
    if q:
        params["q"] = q
    return params


def extract_news(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Top articles as lean rows: title, description (summary), source, date, link, image."""
    rows: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if len(rows) >= _MAX_ROWS:
            break
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        # Skip junk rows newsdata occasionally returns (missing or numeric-only "titles").
        if not title or title.replace(" ", "").isdigit():
            continue
        rows.append(
            _clean(
                {
                    "title": _trim(title, 180),
                    "description": _trim(item.get("description"), 400) or None,
                    "source": item.get("source_name") or item.get("source_id"),
                    "icon": item.get("source_icon"),
                    "date": item.get("pubDate"),
                    "link": item.get("link"),
                    "image": item.get("image_url"),
                }
            )
        )
    return rows
