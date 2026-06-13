"""Zomato placeholder resolver — name-aware ``__RESTAURANT_ID__`` learning.

Zomato's ``get_restaurants_for_keyword`` is fuzzy: a search for a SPECIFICALLY
NAMED outlet ("menu of Ramen Republic, Sector 31") can rank a different
restaurant first, and the default first-hit learner then fetches the wrong
menu (prod chat d21a895d: "Ramen Republic" → an all-biryani menu at
res_id 22197705). When the search keyword spells out a restaurant's full name,
learn THAT restaurant's res_id instead of index 0. Generic cuisine/dish
searches ("pizza", "best biryani") never spell out a single outlet name, so
they keep the fuzzy-ranked first hit — the existing behaviour.
"""

from __future__ import annotations

import re
from typing import Any

from core.providers.mcp.placeholders import (
    DEFAULT_ALIASES,
    DEFAULT_ISSUERS,
    PlaceholderResolver,
)

_KEYWORD_SEARCH = "get_restaurants_for_keyword"
_KEYWORD_ARGS = ("keyword", "query")
_RES_ID_ALIASES = DEFAULT_ALIASES["__RESTAURANT_ID__"]
# In-turn only: the search keyword, stashed so `learn` can name-match against
# the response. Transient → never persisted across turns (mcp_base filters it).
_NAME_HINT = "__RESTAURANT_NAME_HINT__"
_STOPWORDS = frozenset({"the", "a", "an", "and"})


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOPWORDS]


def _restaurant_rows(payload: Any) -> list[tuple[str, str]]:
    """(name, res_id) pairs from a search envelope, in server rank order, deduped."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    stack: list[Any] = [payload.get("data") if isinstance(payload, dict) else None]
    while stack:
        obj = stack.pop(0)
        if isinstance(obj, dict):
            name = obj.get("name")
            rid = ""
            for alias in _RES_ID_ALIASES:
                v = str(obj.get(alias) or "").strip()
                if v:
                    rid = v
                    break
            if isinstance(name, str) and name.strip() and rid and rid not in seen:
                seen.add(rid)
                rows.append((name.strip(), rid))
            stack.extend(obj.values())
        elif isinstance(obj, list):
            stack.extend(obj)
    return rows


def _name_matched_res_id(payload: Any, keyword: str) -> str | None:
    """res_id whose full name the keyword spells out (name tokens ⊆ keyword tokens).

    Picks the most specific (longest-name) such match; returns None when the
    keyword names no single outlet (a generic cuisine/dish search), so the
    caller keeps the fuzzy-ranked first hit.
    """
    kw = set(_tokens(keyword))
    if len(kw) < 2:
        return None
    best: tuple[int, str] | None = None
    for name, rid in _restaurant_rows(payload):
        name_tokens = set(_tokens(name))
        # Require a multi-word name fully spelled out in the query — a single
        # generic word ("pizza") must never hijack the ranked first hit.
        if len(name_tokens) >= 2 and name_tokens <= kw:
            score = len(name_tokens)
            if best is None or score > best[0]:
                best = (score, rid)
    return best[1] if best else None


class ZomatoResolver(PlaceholderResolver):
    """Shared resolver + name-aware ``__RESTAURANT_ID__`` selection for fuzzy search."""

    transient = frozenset({_NAME_HINT})

    def __init__(self) -> None:
        super().__init__(aliases=DEFAULT_ALIASES, issuers=DEFAULT_ISSUERS)

    def learn_from_args(self, values: dict[str, str], tool: str, args: dict[str, Any]) -> None:
        # Only a keyword search carries a name hint; clear it on every other
        # tool so a stale hint can't bias an unrelated restaurant list.
        if tool == _KEYWORD_SEARCH and isinstance(args, dict):
            for key in _KEYWORD_ARGS:
                text = str(args.get(key) or "").strip()
                if text:
                    values[_NAME_HINT] = text
                    return
        values.pop(_NAME_HINT, None)

    def learn(self, values: dict[str, str], payload: dict[str, Any]) -> dict[str, str]:
        learned: dict[str, str] = {}
        hint = values.get(_NAME_HINT)
        if "__RESTAURANT_ID__" not in values and hint:
            rid = _name_matched_res_id(payload, hint)
            if rid:
                values["__RESTAURANT_ID__"] = rid
                learned["__RESTAURANT_ID__"] = rid
        learned.update(super().learn(values, payload))
        return learned
