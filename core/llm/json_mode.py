"""Robust JSON extraction from LLM output.

Ported verbatim (behaviour-preserving) from `app/services/food_llm_router.py`.
LLM "give me JSON" responses arrive in many shapes — pure JSON, markdown-fenced,
JSON wrapped in prose. Strict `json.loads` only handles the first, so we try
strict, then peel a single outer fence, then walk for the first balanced object.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Single outer fence, optional language tag — anchored to the whole string so we
# never strip a fence that appears mid-text.
_WHOLE_FENCE_RE = re.compile(
    r"\A\s*```[a-zA-Z0-9_-]*\s*\n?(?P<body>.*?)\n?\s*```\s*\Z",
    re.DOTALL,
)


def _strip_outer_code_fence(text: str) -> str:
    """Return the inner body if `text` is entirely one markdown fence; else no-op."""
    match = _WHOLE_FENCE_RE.match(text or "")
    return match.group("body") if match else (text or "")


def _extract_first_balanced_json(text: str) -> str | None:
    """Return the first brace-balanced `{...}` substring, ignoring braces in strings."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None  # unbalanced — caller surfaces a clean error


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerant of fences and prose.

    Strict-first (zero overhead on the happy path), then relax. Raises
    `ValueError` when no JSON object can be recovered.
    """
    raw = content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        unfenced = _strip_outer_code_fence(raw)
        try:
            data = json.loads(unfenced)
        except json.JSONDecodeError as exc:
            extracted = _extract_first_balanced_json(unfenced) or _extract_first_balanced_json(raw)
            if not extracted:
                logger.warning("llm json: no balanced object in %r", raw[:600])
                raise ValueError("Model returned invalid JSON.") from exc
            try:
                data = json.loads(extracted)
            except json.JSONDecodeError as exc2:
                logger.warning("llm json: invalid (%s) in %r", exc2, raw[:600])
                raise ValueError("Model returned invalid JSON.") from exc2

    if not isinstance(data, dict):
        raise ValueError("Model payload must be a JSON object.")
    return data
