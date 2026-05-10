from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


def _extract_primary_address(zomato: dict[str, Any] | None) -> str | None:
    if not isinstance(zomato, dict):
        return None

    for step in zomato.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tool") not in ("get_saved_addresses_for_user", "get_saved_addresses_for_user(auto)"):
            continue
        payload = ((step.get("result") or {}).get("data") or {})
        result = payload.get("result") if isinstance(payload, dict) else {}
        addresses = result.get("addresses") if isinstance(result, dict) else None
        if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict):
            location_name = str(addresses[0].get("location_name") or "").strip()
            if location_name:
                return location_name
    return None


def _extract_restaurants(zomato: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(zomato, dict):
        return []

    found: list[dict[str, Any]] = []
    for step in zomato.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "get_restaurants_for_keyword":
            continue
        payload = ((step.get("result") or {}).get("data") or {})
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        for r in results:
            if not isinstance(r, dict):
                continue
            items = r.get("items")
            top_item = None
            if isinstance(items, list) and items and isinstance(items[0], dict):
                top_item = items[0].get("name")
            found.append(
                {
                    "name": str(r.get("name") or "Unknown"),
                    "rating": r.get("rating"),
                    "distance": r.get("distance"),
                    "eta": r.get("eta"),
                    "offer": r.get("res_offer"),
                    "top_item": top_item,
                }
            )
    return found


def build_zomato_summary(zomato: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(zomato, dict):
        return {
            "ok": False,
            "error": "zomato_not_executed",
            "address": None,
            "top_restaurants": [],
            "count": 0,
            "diagnostics": {},
        }

    restaurants = _extract_restaurants(zomato)
    summary: dict[str, Any] = {
        "ok": bool(zomato.get("ok")),
        "error": None if zomato.get("ok") else str(zomato.get("error_detail") or "unknown_error"),
        "address": _extract_primary_address(zomato),
        "top_restaurants": restaurants[:8],
        "count": len(restaurants),
        "diagnostics": zomato.get("diagnostics") if isinstance(zomato.get("diagnostics"), dict) else {},
    }
    return summary


def _fallback_assistant_message(
    *,
    plan: dict[str, Any],
    zomato_summary: dict[str, Any],
) -> str:
    summary = str((plan or {}).get("summary") or "").strip()
    if not zomato_summary.get("ok"):
        detail = str(zomato_summary.get("error") or "Unknown tool execution error.")
        return (
            "I understood your request"
            + (f": {summary}." if summary else ".")
            + f" But I hit an issue while talking to Zomato tools: {detail}. "
            + "Would you like me to retry or refine your request?"
        )

    restaurants = zomato_summary.get("top_restaurants") or []
    if not restaurants:
        return (
            "I understood your request"
            + (f": {summary}." if summary else ".")
            + " I could not find strong matches yet. "
            + "Would you like me to refine by cuisine, budget, veg preference, or rating?"
        )

    top = restaurants[:3]
    names = ", ".join(str(r.get("name") or "Unknown") for r in top)
    return (
        "Here are good options"
        + (f": {summary}." if summary else ".")
        + f" Top picks: {names}. "
        + "What should I do next? You can ask for menu, filters, or add an item to cart."
    )


def build_assistant_message_with_llm(
    *,
    user_message: str,
    plan: dict[str, Any],
    zomato_summary: dict[str, Any],
    provider: str,
    api_key: str,
    model: str,
) -> str:
    system_prompt = (
        "You are a food ordering assistant. Convert structured tool output into a user-facing response.\n"
        "Rules:\n"
        "1) Be concise and clear.\n"
        "2) Mention top options only if present.\n"
        "3) Never expose internal tool/debug data.\n"
        "4) End with one clear next-step question.\n"
        "5) Include 2-4 concrete suggestions the user can reply with.\n"
    )

    payload = {
        "user_message": user_message,
        "routing_plan": {
            "intent": plan.get("intent"),
            "summary": plan.get("summary"),
            "constraints": plan.get("constraints"),
        },
        "zomato_summary": zomato_summary,
    }

    user_prompt = (
        "Using this JSON, write the assistant reply to the user:\n"
        + json.dumps(payload, ensure_ascii=True)
    )

    try:
        if provider == "openai":
            client = OpenAI(api_key=api_key)
            completion = client.chat.completions.create(
                model=model,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            text = completion.choices[0].message.content or ""
            text = text.strip()
            if text:
                return text
            return _fallback_assistant_message(plan=plan, zomato_summary=zomato_summary)

        from google import genai as google_genai

        client = google_genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}",
        )
        text = (response.text or "").strip()
        if text:
            return text
        return _fallback_assistant_message(plan=plan, zomato_summary=zomato_summary)
    except Exception:
        return _fallback_assistant_message(plan=plan, zomato_summary=zomato_summary)
