from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from app.services.llm_config import ResolvedLlm

logger = logging.getLogger(__name__)

_PRODUCT_HINTS = {
    "auto": "auto",
    "rickshaw": "auto",
    "go": "ubergo",
    "ubergo": "ubergo",
    "premier": "premier",
    "xl": "xl",
    "suv": "xl",
    "intercity": "intercity",
}


def _heuristic_extract(message: str) -> dict[str, Any]:
    text = (message or "").strip()
    lowered = text.lower()
    action = "book"
    if re.search(r"\b(cancel|stop)\b", lowered):
        action = "cancel"
    elif re.search(r"\b(status|where|track)\b", lowered):
        action = "status"
    elif re.search(r"\b(estimate|fare|price|cost|how much)\b", lowered):
        action = "estimate"

    product_hint = None
    for needle, value in _PRODUCT_HINTS.items():
        if re.search(rf"\b{re.escape(needle)}\b", lowered):
            product_hint = value
            break

    destination = text
    for pattern in (
        r"\b(?:to|for)\s+(.+)$",
        r"\b(?:book|call|get|order)\s+(?:me\s+)?(?:an?\s+)?(?:uber|cab|taxi|ride)?\s*(?:to\s+)?(.+)$",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            destination = match.group(1)
            break
    destination = re.sub(r"\b(?:please|now|asap|right now)\b", "", destination, flags=re.IGNORECASE).strip(" .,;:-")

    return {
        "action": action,
        "pickup_text": "",
        "destination_text": destination,
        "product_hint": product_hint,
        "seat_count": None,
        "request_id": None,
    }


_SYSTEM_PROMPT = """You extract Uber cab booking intent for India.
Return strict JSON with exactly:
{
  "action": "estimate" | "book" | "status" | "cancel",
  "pickup_text": "<pickup place if user names one, else empty string>",
  "destination_text": "<dropoff place/address if applicable, else empty string>",
  "product_hint": "auto" | "ubergo" | "premier" | "xl" | "intercity" | null,
  "seat_count": integer | null,
  "request_id": "<Uber request id if user provided one, else null>"
}
Rules:
- Cab/ride/taxi/Uber requests are cab booking.
- If pickup is "from here/current location/my location", leave pickup_text empty.
- Prefer action=book when user says book/call/get me a cab.
- Prefer action=estimate when user asks fare/price/cost/ETA options.
- India product hints: auto/rickshaw -> auto, Uber Go -> ubergo, Premier -> premier.
"""


def _extract_with_llm(*, user_message: str, llm: ResolvedLlm) -> dict[str, Any] | None:
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
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message.strip()},
            ],
        )
        content = (completion.choices[0].message.content or "").strip()
        return json.loads(content) if content else None
    except Exception:
        logger.debug("Uber planner LLM extraction failed; using heuristic.", exc_info=True)
        return None


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action") or "book").strip().lower()
    if action not in {"estimate", "book", "status", "cancel"}:
        action = "book"
    product_hint = raw.get("product_hint")
    if isinstance(product_hint, str):
        product_hint = product_hint.strip().lower() or None
    if product_hint not in {None, "auto", "ubergo", "premier", "xl", "intercity"}:
        product_hint = None
    seat_count = raw.get("seat_count")
    try:
        seat_count = int(seat_count) if seat_count is not None else None
    except (TypeError, ValueError):
        seat_count = None
    return {
        "intent": "cab_booking",
        "summary": "Uber cab booking for India",
        "action": action,
        "pickup_text": str(raw.get("pickup_text") or "").strip(),
        "destination_text": str(raw.get("destination_text") or "").strip(),
        "product_hint": product_hint,
        "seat_count": seat_count,
        "request_id": str(raw.get("request_id") or "").strip() or None,
        "provider": "uber",
    }


def plan_uber_cab(*, user_message: str, llm: ResolvedLlm | None = None, reasoning: str = "") -> dict[str, Any]:
    raw = _extract_with_llm(user_message=user_message, llm=llm) if llm else None
    plan = _normalise(raw if isinstance(raw, dict) else _heuristic_extract(user_message))
    if reasoning:
        plan["summary"] = reasoning
    return plan
