"""Bounded self-repair for tool execution (the one recovery layer all providers share).

Two tiers, mirroring production agent practice (Reflexion / Instructor / LangGraph):
  * Tier 1 — deterministic, full-jitter backoff for transient transport failures.
  * Tier 2 — ONE LLM pass that feeds a server validation error verbatim, plus the
    tool's JSON schema and the failed arguments, back to the model for corrected
    arguments. Hard budgets; on exhaustion the original typed failure surfaces.

Decision rules: auth + confirmation gates never retry; unresolved placeholders and
empty-but-successful results are never "repaired"; non-idempotent tools never
re-execute after a timeout (the first attempt may have landed server-side).
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

from core.contracts.tool_result import ErrorCode

logger = logging.getLogger(__name__)

MAX_REPAIRS_PER_TURN = 2

_VALIDATION_SIGNS = re.compile(
    r"validation error|unexpected keyword|extra fields not permitted|field required"
    r"|is a required property|input should be|invalid argument|additional properties",
    re.IGNORECASE,
)

_REPAIR_PROMPT = (
    "A tool call was rejected by the server. Fix the arguments so the call passes "
    "the tool's JSON schema while preserving the original intent. Use the server "
    "error verbatim to locate the problem (wrong nesting, unknown keys, missing "
    "required fields). Never invent ids or values you cannot derive from the "
    "failed arguments. "
    'Respond ONLY with JSON: {"arguments": {...}} '
    'or {"give_up": true, "reason": "<why>"}.'
)


def error_text(envelope: Any) -> str:
    """The server's error message out of a parsed MCP envelope, best-effort."""
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if isinstance(data, dict):
        for key in ("raw_text", "error", "message", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    try:
        return json.dumps(data, ensure_ascii=False)[:500] if data is not None else ""
    except (TypeError, ValueError):
        return str(data)[:500]


def classify_envelope(envelope: Any) -> ErrorCode:
    """INVALID_ARGS when the server text reads like schema validation, else UPSTREAM_ERROR."""
    if _VALIDATION_SIGNS.search(error_text(envelope)):
        return ErrorCode.INVALID_ARGS
    return ErrorCode.UPSTREAM_ERROR


def backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 2.0) -> float:
    """Full-jitter exponential backoff (AWS pattern): random(0, min(cap, base*2^n))."""
    return random.uniform(0, min(cap, base * (2**attempt)))


@dataclass
class RepairBudget:
    """Per-turn repair cap, shared across the turn's providers via ExecCtx."""

    remaining: int = MAX_REPAIRS_PER_TURN

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


async def repair_arguments(
    *,
    tool: str,
    schema: dict[str, Any] | None,
    failed_args: dict[str, Any],
    server_error: str,
    intent: str | None,
    llm: Any,
    complete_json: Any,
) -> dict[str, Any] | None:
    """One bounded LLM repair pass. Returns corrected arguments, or None.

    Guards: tool name immutable, identical args rejected (would fail identically),
    output locally validated against the tool schema before it is ever re-sent."""
    user_message = json.dumps(
        {
            "tool": tool,
            "intent": intent or "",
            "failed_arguments": failed_args,
            "server_error": (server_error or "")[:1000],
            "json_schema": schema or {},
        },
        ensure_ascii=False,
    )
    try:
        out = await complete_json(user_message=user_message, system_prompt=_REPAIR_PROMPT, llm=llm)
    except Exception:
        logger.warning("repair: LLM pass failed for %s", tool, exc_info=True)
        return None
    if not isinstance(out, dict) or out.get("give_up"):
        return None
    repaired = out.get("arguments")
    if not isinstance(repaired, dict) or repaired == failed_args:
        return None
    if schema:
        try:
            import jsonschema

            jsonschema.validate(repaired, schema)
        except ImportError:
            pass
        except Exception:
            logger.info("repair: discarded schema-invalid repair for %s", tool)
            return None
    logger.info("repair: %s args repaired (error was: %s)", tool, (server_error or "")[:200])
    return repaired
