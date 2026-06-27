"""Shared LLM call helper for every agent role (Sarvam, strict JSON-object mode).

Each role module (router, grocery_brain, grocery_response, plus legacy resolve/narrate) builds its own
payload + system prompt and calls `_complete`. Degrades gracefully to `{}` if the LLM is unavailable or
errors, so a role can always fall back to a deterministic default.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.llm.client import complete_json_object
from core.llm.config import resolve_llm_from_env

logger = logging.getLogger(__name__)


async def _complete(system: str, payload: Any) -> dict[str, Any]:
    llm, reason = resolve_llm_from_env()
    if llm is None:
        logger.warning("agent LLM unavailable: %s", reason)
        return {}
    user = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    try:
        out = await complete_json_object(user_message=user, system_prompt=system, llm=llm)
        return out if isinstance(out, dict) else {}
    except Exception:
        logger.exception("agent LLM call failed")
        return {}
