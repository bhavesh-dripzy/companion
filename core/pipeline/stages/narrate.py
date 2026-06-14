"""Stage 6 — Narrate: response LLM -> the assistant's reply.

Honours `reply_language` (voice turns pass "hinglish"). Provider-specific card
filtering (featured_variant_ids / show_cart) is layered on by plugins; the base
narrator produces the message text and degrades gracefully if the LLM is absent.
"""

from __future__ import annotations

import json
from typing import Any

from core.pipeline.context import PipelineContext

_RESPONSE_PROMPT = (
    "You are Companion, a concise, friendly assistant.\n"
    "Given the conversation so far and any tool results, write a short, helpful "
    "reply to the latest user message. Use the earlier turns for context — names, "
    "preferences, and what was already shown (e.g. items or restaurants) carry over; "
    "do not claim the user never told you something they said earlier in the chat.\n"
    "Successful results are rendered to the user as rich cards beside your reply "
    "(the 'Cards rendered' line lists them) — summarize them confidently and point "
    "at the cards; never enumerate every row.\n"
    "Every tool result is tagged with its platform ([<platform>.<tool>]), and the "
    "'Cards rendered' lines name the platform each card set came from. Attribute "
    "every fact (items, prices, ratings, menus) to its tagged platform — NEVER "
    "present one platform's data as another's. When the user asks to compare "
    "platforms, report each side strictly from its own tagged results; if one "
    "side's tools failed or returned nothing, say that about that platform only — "
    "do not claim a platform had no results while its cards are being rendered.\n"
    "If some tools failed while others succeeded, lead with what succeeded and "
    "mention the failure only if it blocks what the user asked for. NEVER give a "
    "blanket 'technical issue' apology while results are being shown.\n"
    "When a grocery provider (swiggy_instamart or zepto) fails with a "
    "NOT_SERVICEABLE error (address not in delivery zone, item out of stock, or "
    "area not covered), explicitly tell the user that platform cannot deliver to "
    "their address, then suggest trying the other grocery platform by name — e.g. "
    "if Instamart is not serviceable suggest Zepto, and vice versa. Keep it brief "
    "and actionable: one sentence on why it failed, one sentence suggesting the alternative.\n"
    "You DO have live web access through tools: news/headlines on any topic, "
    "video search, flights, hotels, product prices, maps, stock quotes, AND "
    "outbound phone calls (via make_phone_call — speaks a message to any number). "
    "If the tool results show a make_phone_call that succeeded, confirm the call "
    "was placed. If it was blocked pending confirmation, ask the user to confirm. "
    "If it failed with an error, tell the user the call could not be placed and "
    "suggest trying again. NEVER claim a call was placed when the tool results "
    "show it blocked or failed.\n"
    "NEVER tell the user you lack real-time news, web, or video access, and never "
    "cite a knowledge cutoff. If this turn ran no tools but the user asked for "
    "something fetchable (latest news, videos to watch, prices, etc.), do NOT "
    "refuse or redirect them to another site — acknowledge you can look it up and "
    "offer to pull it right now (e.g. 'Want me to fetch the latest? Just say the "
    "word'). If the user says you can do something you just declined, agree and "
    "offer to do it — never double down on a false 'I can't'.\n"
    "An empty result list from a successful tool is valid data, not an error — say "
    "what it means using facts from the results (e.g. 'no showtimes today; it "
    "releases on 17 July'), and suggest the natural next step.\n"
    "Respect release dates: if a result carries a release_date LATER than today's "
    "date (shown in the clock line above), that title is NOT yet showing — say it "
    "'releases on <date>', never 'now showing' / 'currently playing' / 'in "
    "cinemas'. Only call a title currently showing when it has actual showtimes or "
    "a release_date today or earlier.\n"
    "If an asked-about item is missing from this turn's results but appears in the "
    "'already seen earlier this session' lines, use that known price (note it was "
    "seen earlier) instead of claiming the item doesn't exist.\n"
    "Address the user by the name THEY used for themselves in the conversation, "
    "even if a different name appears in the task description or earlier turns.\n"
    "Reply ONLY in this language/style: {language}.\n"
    "If tools were blocked pending confirmation, ask the user to confirm.\n"
    'Respond with ONLY a JSON object: {{"message": "<reply>"}}.'
)


def _conversation(ctx: PipelineContext) -> str:
    """Recent turns + the current message, so the narrator has multi-turn context.

    The LLM helper is single-turn, so history is folded into the prompt text —
    the same shape the Plan stage uses (history[-8:] as ``role: content`` lines)."""
    from core.pipeline.clock import clock_line

    lines: list[str] = [clock_line()]
    for turn in ctx.request.history[-8:]:
        role = str(turn.get("role") or "user")
        content = str(turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append(f"user: {ctx.request.message}")
    return "Conversation so far:\n" + "\n".join(lines)


# Fields that label one card entry, in priority order. Spans every card kind
# so the narrator's ground-truth line names entries from movies, shows, events,
# flights, videos, news, instruments, … not just food rows.
_ENTRY_LABEL_FIELDS = (
    "name",
    "item_name",
    "title",
    "movie_name",
    "theatre_name",
    "venue_name",
    "tradingsymbol",
    "place_name",
    "code",
    "fund",
)
# Top-level wire keys that are never the entry list.
_NON_ENTRY_KEYS = frozenset({"kind", "source", "provider", "mime_type"})


def _entry_list(wire: dict[str, Any]) -> list[Any]:
    """The card's entry list, whatever field holds it (restaurants / movies /
    flight_options / videos / …). Picks the longest top-level list-of-dicts so
    any card kind — current or future — yields its true entry count without
    enumerating field names. Fixes the narrator misreading multi-entry movie /
    show / flight / video cards as '0 entries' → 'nothing found'."""
    best: list[Any] = []
    for key, value in wire.items():
        if key in _NON_ENTRY_KEYS:
            continue
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and len(value) > len(best)
        ):
            best = value
    return best


def _entry_label(entry: dict[str, Any]) -> str:
    for field in _ENTRY_LABEL_FIELDS:
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _card_line(attachment: Any) -> str:
    """One 'Cards rendered' line: platform, kind, and the top entry names —
    ground truth the narrator can cite even when raw tool blobs are truncated."""
    wire = attachment.to_wire()
    source = str(wire.get("source") or wire.get("provider") or "").split(".")[0] or "unknown"
    entries = _entry_list(wire)
    names = [_entry_label(e) for e in entries[:6] if isinstance(e, dict)]
    names = [n for n in names if n]
    extra = f", +{len(entries) - 6} more" if names and len(entries) > 6 else ""
    tail = f": {', '.join(names)}{extra}" if names else ""
    return f"  - [{source}] {wire.get('kind')} ({len(entries)} entries){tail}"


def _result_blob(ctx: PipelineContext, provider_id: str, tool_result: Any) -> str:
    """Provider digest when available (lean but COMPLETE), else truncated raw JSON."""
    provider = ctx.deps.registry.get(provider_id)
    summarize = getattr(provider, "summarize_result", None)
    if callable(summarize):
        try:
            digest = summarize(tool_result)
            if digest:
                return digest
        except Exception:
            pass
    data = tool_result.data
    return json.dumps(data)[:1500] if data is not None else ""


def _session_knowledge(ctx: PipelineContext) -> list[str]:
    """Menu prices already learned this session, platform-tagged and grouped by
    restaurant — so the narrator never reports a known item as 'not found'."""
    legacy = ctx.session.legacy if isinstance(ctx.session.legacy, dict) else {}
    menu = legacy.get("session_menu")
    if not isinstance(menu, list) or not menu:
        return []
    restaurants = legacy.get("session_restaurants")
    names = {
        str(r.get("res_id")): str(r.get("name"))
        for r in (restaurants if isinstance(restaurants, list) else [])
        if isinstance(r, dict) and r.get("res_id") and r.get("name")
    }
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in menu[:48]:
        if not isinstance(row, dict):
            continue
        item = str(row.get("item_name") or "").strip()
        price = row.get("discounted_price") or row.get("price")
        if not item or price in (None, "", 0):
            continue
        key = (str(row.get("provider") or "unknown"), str(row.get("res_id") or ""))
        entry = f"{item} ₹{price}"
        if entry not in grouped.setdefault(key, []):
            grouped[key].append(entry)
    if not grouped:
        return []
    lines = ["Item prices already seen earlier this session (may be stale; prefer fresh results):"]
    for (provider, rid), items in grouped.items():
        label = names.get(rid) or (f"res_id={rid}" if rid else "items")
        lines.append(f"  - [{provider}] {label}: {'; '.join(items[:8])}")
    return lines


def _results_summary(ctx: PipelineContext) -> str:
    parts: list[str] = []
    for result in ctx.provider_results:
        for tool_result in result.results:
            if tool_result.ok:
                status = "ok"
            else:
                status = f"error:{tool_result.error}"
                if tool_result.error and tool_result.error.value == "not_serviceable":
                    status = "NOT_SERVICEABLE"
            blob = _result_blob(ctx, result.provider, tool_result)
            parts.append(f"[{result.provider}.{tool_result.tool} {status}] {blob}")
    if ctx.blocked:
        parts.append("Blocked pending confirmation: " + ", ".join(c.tool for c in ctx.blocked))
    if ctx.attachments:
        parts.append("Cards rendered to the user this turn:")
        parts.extend(_card_line(a) for a in ctx.attachments)
    parts.extend(_session_knowledge(ctx))
    return "\n".join(parts) or "(no tools ran)"


def _fallback_message(ctx: PipelineContext) -> str:
    if ctx.blocked:
        return "Please confirm and I'll go ahead."
    if any(r.failed for r in ctx.provider_results):
        return "Sorry, I hit an error reaching that service. Want to try again?"
    return "Done."


class Narrate:
    name = "narrate"

    async def run(self, ctx: PipelineContext) -> None:
        language = ctx.request.reply_language or "English"
        system = _RESPONSE_PROMPT.format(language=language)
        user = f"{_conversation(ctx)}\n\nTool results:\n{_results_summary(ctx)}"
        try:
            raw = await ctx.deps.complete_json(user_message=user, system_prompt=system, llm=ctx.llm)
            ctx.assistant_message = str(raw.get("message") or "").strip()
        except Exception:
            ctx.assistant_message = ""
        if not ctx.assistant_message:
            ctx.assistant_message = _fallback_message(ctx)
        ctx.tracer.event("narrate", chars=len(ctx.assistant_message))
