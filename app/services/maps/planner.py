"""Maps domain agent — extract destination + travel mode from a natural-language query.

Mirrors ``app.services.calendar.planner`` in shape: a dumb planner that builds a
tool plan; the actual extraction (destination + mode) happens here so the
executor stays a pure HTTP wrapper around the Routes API.

We use the same LLM the rest of the pipeline already has (no extra dependency)
to extract structured fields. A deterministic regex fallback handles common
phrasings if the LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from app.services.llm_config import ResolvedLlm

logger = logging.getLogger(__name__)


_ALLOWED_TRAVEL_MODES = ("DRIVE", "WALK", "BICYCLE", "TWO_WHEELER", "TRANSIT")

# Phrases that typically precede a destination. Ordered longest-first so the
# regex prefers more specific matches.
_PREFIX_PATTERNS = (
    r"how\s+(?:long|much\s+time)\s+(?:will\s+it\s+take\s+)?(?:to\s+(?:reach|get\s+to|go\s+to|drive\s+to|walk\s+to)\s+)",
    r"how\s+far\s+(?:is\s+)?(?:to\s+|from\s+(?:here|my\s+location)\s+to\s+)",
    r"distance\s+(?:from\s+(?:here|my\s+location)\s+)?to\s+",
    r"eta\s+(?:to|for)\s+",
    r"time\s+to\s+(?:reach|get\s+to|drive\s+to|walk\s+to)\s+",
    r"directions?\s+to\s+",
    r"drive\s+to\s+",
    r"walk\s+to\s+",
    r"bike\s+to\s+",
    r"cycle\s+to\s+",
    r"from\s+(?:here|my\s+location|my\s+current\s+location)\s+to\s+",
    r"to\s+reach\s+",
)

_MODE_HINTS: dict[str, str] = {
    r"\bwalk(?:ing)?\b": "WALK",
    r"\bon\s+foot\b": "WALK",
    r"\bbicycle\b|\bbike\b|\bcycle\b|\bcycling\b": "BICYCLE",
    r"\bscooter\b|\btwo[-\s]?wheeler\b|\bbike\s+ride\b": "TWO_WHEELER",
    r"\bbus\b|\btrain\b|\bmetro\b|\btransit\b|\bpublic\s+transport\b": "TRANSIT",
    r"\bdrive|\bdriving\b|\bcar\b|\bcab\b|\buber\b|\bola\b": "DRIVE",
}

_SUFFIX_TRIM = re.compile(
    r"\b(by\s+(?:car|drive|walk|bike|cycle|bus|train|metro|transit)|on\s+foot|right\s+now|please|now|"
    r"from\s+(?:here|my\s+location|my\s+current\s+location)|\??)$",
    flags=re.IGNORECASE,
)


def _heuristic_extract(user_message: str) -> dict[str, Any]:
    """Best-effort regex extraction used as a fallback when the LLM fails."""
    text = (user_message or "").strip()
    if not text:
        return {"destination": "", "travel_mode": None}

    lowered = text.lower()
    travel_mode: str | None = None
    for pattern, mode in _MODE_HINTS.items():
        if re.search(pattern, lowered):
            travel_mode = mode
            break

    destination = text
    for pattern in _PREFIX_PATTERNS:
        m = re.search(pattern, lowered)
        if m:
            destination = text[m.end():].strip()
            break

    destination = _SUFFIX_TRIM.sub("", destination).strip(" .,;:!?-")
    return {"destination": destination, "travel_mode": travel_mode}


_LLM_SYSTEM_PROMPT = (
    "You extract a destination and a travel mode from the user's message. "
    "Return STRICT JSON with this exact shape — no markdown, no commentary:\n"
    '{ "destination": "<place or address as the user named it, or empty string>",'
    '  "travel_mode": "DRIVE" | "WALK" | "BICYCLE" | "TWO_WHEELER" | "TRANSIT" | null }\n'
    "Rules:\n"
    "- The destination is the place the user wants to go TO. Strip leading verbs "
    "(\"how long to reach\", \"drive to\", \"directions to\"), trailing fillers "
    "(\"please\", \"right now\"), and quoting.\n"
    "- If the user mentions a transport mode (drive/walk/bike/cycle/bus/train/metro/cab), "
    "set travel_mode accordingly. Otherwise return null and let the server default decide.\n"
    "- If you cannot identify a destination, return an empty string."
)


def _extract_with_llm(*, user_message: str, llm: ResolvedLlm) -> dict[str, Any] | None:
    """Try the configured LLM; return ``None`` on any failure."""
    try:
        kwargs: dict[str, Any] = {"api_key": llm.api_key}
        if llm.base_url and llm.provider in {"deepseek", "openai"}:
            kwargs["base_url"] = llm.base_url
        client = OpenAI(**kwargs)
        completion = client.chat.completions.create(
            model=llm.model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": (user_message or "").strip()},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        if not text:
            return None
        return json.loads(text)
    except Exception:
        logger.debug("Maps planner LLM extraction failed; falling back to heuristic.")
        return None


def _normalise(parsed: dict[str, Any]) -> dict[str, Any]:
    dest = str(parsed.get("destination") or "").strip()
    mode_raw = parsed.get("travel_mode")
    mode = str(mode_raw).strip().upper() if isinstance(mode_raw, str) and mode_raw else None
    if mode not in _ALLOWED_TRAVEL_MODES:
        mode = None
    return {"destination": dest, "travel_mode": mode}


def _empty_constraints() -> dict[str, Any]:
    return {
        "requires_address_resolution": False,
        "requested_location_text": None,
        "inferred_keyword": None,
        "price_max_inr": None,
        "price_min_inr": None,
        "min_rating": None,
        "near_and_fast": None,
        "vegetarian_only": None,
        "party_size_estimate": None,
        "health_constraints": None,
    }


def plan_maps_query(
    *,
    reasoning: str,
    user_message: str,
    llm: ResolvedLlm | None = None,
) -> dict[str, Any]:
    """Build a tool_plan for a single Routes-API ``compute_route`` call."""
    parsed = None
    if llm is not None:
        raw = _extract_with_llm(user_message=user_message, llm=llm)
        if raw is not None:
            parsed = _normalise(raw)

    if not parsed or not parsed.get("destination"):
        parsed = _normalise(_heuristic_extract(user_message))

    destination = parsed["destination"]
    travel_mode = parsed["travel_mode"]

    return {
        "intent": "maps_query",
        "summary": reasoning,
        "tool_plan": [
            {
                "tool": "compute_route",
                "provider": "google_maps",
                "arguments": {
                    "destination": destination,
                    "travel_mode": travel_mode,
                },
            }
        ],
        "constraints": _empty_constraints(),
        "execution_notes_for_downstream": (
            "Resolve user's current location (frontend geolocation), then call "
            "Google Maps Routes API for distance + ETA."
        ),
        "validation": {
            "tool_count": 1,
            "dropped_unknown_tool_steps": 0,
            "resolved_model": "orchestrator",
            "completion_id": "",
            "provider": "google_maps",
        },
    }
