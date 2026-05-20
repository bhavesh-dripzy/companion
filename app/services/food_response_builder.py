"""Response-builder agent.

Takes the **raw** tool-execution output (MCP responses) and asks the LLM to write
the user-facing reply. There is intentionally no domain-specific extraction
here — the LLM reads the raw structured tool output directly so this works for
food, groceries, calendar/reminder lookups, medical queries, or any future
provider without code changes.

Only two helpers remain:
- ``saved_address_label_from_mcp_item`` — small utility used by other modules
  (``companion_api/main.py``, swiggy/zomato tests) to format saved-address rows.
- ``wrap_tool_result`` (alias: ``build_zomato_summary`` for back-compat) —
  thin ``{ok, error, raw, diagnostics}`` wrapper around a raw MCP result so the
  API response envelope still surfaces success/failure cleanly without doing
  any lossy extraction.
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


def saved_address_label_from_mcp_item(item: dict[str, Any]) -> str:
    """
    Human-facing label for one saved-address row from MCP (Zomato, Swiggy, etc.).

    Swiggy Food ``get_addresses`` uses camelCase: ``addressLine``, ``addressTag``,
    ``addressCategory``, ``id`` — not ``location_name`` / ``address_line``.
    """
    if not isinstance(item, dict):
        return ""
    row_id = str(item.get("address_id") or item.get("addressId") or item.get("id") or "").strip()

    def ok(text: str) -> bool:
        t = (text or "").strip()
        return bool(t) and t != row_id

    tag = str(item.get("addressTag") or item.get("address_tag") or "").strip()
    category = str(item.get("addressCategory") or item.get("address_category") or "").strip()
    line = str(
        item.get("addressLine")
        or item.get("address_line")
        or item.get("formatted_address")
        or item.get("formattedAddress")
        or item.get("location_name")
        or item.get("name")
        or item.get("label")
        or ""
    ).strip()

    if tag and line:
        return f"{tag} — {line}" if ok(line) else tag
    if line and ok(line):
        return line
    if tag:
        return tag
    if category and line:
        return f"{category}: {line}"
    if category:
        return category
    for key in ("location_name", "name", "label"):
        v = str(item.get(key) or "").strip()
        if ok(v):
            return v
    return ""


def wrap_tool_result(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Thin pass-through wrapper around a raw MCP/tool result.

    Surfaces ``ok``, ``error``, and ``diagnostics`` at the top level so the
    response envelope and downstream consumers don't have to dig, while
    preserving the full raw payload under ``raw_result`` for the response LLM
    to read directly.
    """
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "error": "tool_not_executed",
            "raw_result": None,
            "diagnostics": {},
        }
    return {
        "ok": bool(raw.get("ok")),
        "error": None if raw.get("ok") else str(raw.get("error_detail") or "unknown_error"),
        "raw_result": raw,
        "diagnostics": raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {},
    }


def build_zomato_summary(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Backwards-compatible alias for ``wrap_tool_result``.

    Existing callers (pipeline, food_agent view, API response envelope) still
    import this name; the implementation no longer performs any extraction.
    """
    return wrap_tool_result(raw)


def _format_conversation_history(
    conversation_history: list[dict[str, str]] | None,
) -> str | None:
    """Render the last few turns of conversation as a compact transcript."""
    if not conversation_history:
        return None
    recent = [
        item
        for item in conversation_history[-8:]
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and item.get("content")
    ]
    if not recent:
        return None
    return "\n".join(
        f"{item.get('role')}: {str(item.get('content') or '')[:700]}"
        for item in recent
    )


def _any_tool_succeeded(tool_results: dict[str, Any] | None) -> bool:
    if not isinstance(tool_results, dict):
        return False
    for value in tool_results.values():
        if isinstance(value, dict) and value.get("ok"):
            return True
    return False


def _fallback_assistant_message(
    *,
    tool_results: dict[str, Any] | None,
) -> str:
    """Domain-neutral safety-net reply used only when the response LLM call fails.

    Real phrasing lives in the LLM prompt; this is intentionally minimal.
    """
    if not _any_tool_succeeded(tool_results):
        return (
            "I tried to take care of that for you, but couldn't finish it this time. "
            "Want me to try again, or would you like to refine the request?"
        )
    return (
        "I've got something back for you on that. "
        "Should I show more details, refine the search, or move ahead?"
    )


def _openai_compatible_chat(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    base_url: str | None = None,
) -> str:
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    completion = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (completion.choices[0].message.content or "").strip()


_SYSTEM_PROMPT = (
    "You are the user's personal AI voice assistant. Your job is to take action on "
    "their behalf — across any domain they ask about (ordering food or groceries, "
    "scheduling, reminders, general questions, medical or health queries, lookups, "
    "planning, research, and more). One or more of your connected tools has just "
    "run for the user, and the structured tool output is given to you as JSON. "
    "Write the spoken reply.\n"
    "\n"
    "How to write the reply:\n"
    "- Speak in first person, as the assistant who just did the work for them. "
    "Phrases in the spirit of \"Here's what I found for you on <provider>\", "
    "\"I checked <provider> and got these\", or \"Maine yeh dhundha aapke liye\" "
    "feel right. Make it sound like an assistant doing the work, not a search engine.\n"
    "- Use the conversation history (when provided) to stay coherent with what the "
    "user was already asking. Don't repeat yourself, don't re-ask things they "
    "already answered.\n"
    "- Match the user's language and register. If they wrote in Hinglish, reply in "
    "Hinglish. If they wrote in English, reply in English. Same for any other "
    "language. Keep it warm, natural, and short — this will be read out loud.\n"
    "- Treat the tool JSON as ground truth. Read it carefully — it can be deeply "
    "nested. Pull out whatever is genuinely useful to the user (names, prices, "
    "ratings, ETAs, addresses, times, dosages, links, identifiers the user would "
    "recognise, etc.). Do not invent anything that isn't there. If a field is "
    "missing or empty, don't fill it in.\n"
    "- Never expose raw JSON, field names, internal IDs the user wouldn't recognise, "
    "tool/provider IDs, MCP terminology, error codes, stack traces, or anything "
    "that sounds technical.\n"
    "- When the tool returned useful results, surface the top 3-5 most relevant "
    "items in one short list. For each item, mention the single most useful "
    "attribute available — pick whatever genuinely helps the user decide.\n"
    "- When the tool failed or returned nothing useful, acknowledge it gracefully "
    "in plain language, and give the user a clear next step.\n"
    "- Always end with a short, interactive follow-up — 2 to 4 concrete spoken "
    "suggestions the user can reply with, chosen to fit the data and the user's "
    "likely intent. They should feel like things a helpful assistant would offer "
    "next, e.g. \"should I show you the full menu of <X>?\", \"want me to filter "
    "only veg?\", \"shall I add this to your cart?\", \"kuch aur dhundhun?\", "
    "\"aapka kuch khaane ka man hai?\", \"should I book this for you?\", etc. — "
    "always adapted to the actual domain and result.\n"
    "- Keep the whole reply short enough to feel comfortable as a voice response."
)


def build_assistant_message_with_llm(
    *,
    user_message: str,
    plan: dict[str, Any] | None,
    tool_results: dict[str, Any] | None,
    provider: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Generate the final assistant reply from raw tool output.

    Parameters
    ----------
    user_message:
        Current user turn (just this turn, untransformed).
    plan:
        Planner output (intent / summary / constraints) from the domain agent —
        gives the response LLM context on *why* the tool was called.
    tool_results:
        Mapping of provider id (e.g. ``"zomato"``, ``"swiggy_food"``) to the
        **raw** MCP/tool result. No extraction is done here — the LLM reads it.
    conversation_history:
        Recent ``user`` / ``assistant`` turns so the reply stays coherent with
        the ongoing conversation.
    """
    history_transcript = _format_conversation_history(conversation_history)

    plan_context: dict[str, Any] | None = None
    if isinstance(plan, dict):
        plan_context = {
            "intent": plan.get("intent"),
            "summary": plan.get("summary"),
            "constraints": plan.get("constraints"),
        }

    payload: dict[str, Any] = {
        "current_user_message": user_message,
        "conversation_history": history_transcript,
        "planner_context": plan_context,
        "tool_results": tool_results or {},
    }

    user_prompt = (
        "Here is the structured tool output along with conversation context. "
        "Write the assistant's spoken reply to the user:\n"
        + json.dumps(payload, ensure_ascii=True, default=str)
    )

    try:
        if provider in ("openai", "deepseek"):
            text = _openai_compatible_chat(
                api_key=api_key,
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                base_url=base_url if provider == "deepseek" else None,
            )
            if text:
                return text
            return _fallback_assistant_message(tool_results=tool_results)

        from google import genai as google_genai

        client = google_genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=f"SYSTEM:\n{_SYSTEM_PROMPT}\n\nUSER:\n{user_prompt}",
        )
        text = (response.text or "").strip()
        if text:
            return text
        return _fallback_assistant_message(tool_results=tool_results)
    except Exception:
        return _fallback_assistant_message(tool_results=tool_results)
