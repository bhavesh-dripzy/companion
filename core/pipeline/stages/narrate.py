"""Stage 6 — Narrate: response LLM -> the assistant's reply.

Honours `reply_language` (voice turns pass "hinglish"). Provider-specific card
filtering (featured_variant_ids / show_cart) is layered on by plugins; the base
narrator produces the message text and degrades gracefully if the LLM is absent.
"""

from __future__ import annotations

import json

from core.pipeline.context import PipelineContext

_RESPONSE_PROMPT = (
    "You are Companion, a concise, friendly assistant.\n"
    "Given the conversation so far and any tool results, write a short, helpful "
    "reply to the latest user message. Use the earlier turns for context — names, "
    "preferences, and what was already shown (e.g. items or restaurants) carry over; "
    "do not claim the user never told you something they said earlier in the chat.\n"
    "Reply ONLY in this language/style: {language}.\n"
    "If tools were blocked pending confirmation, ask the user to confirm.\n"
    'Respond with ONLY a JSON object: {{"message": "<reply>"}}.'
)


def _conversation(ctx: PipelineContext) -> str:
    """Recent turns + the current message, so the narrator has multi-turn context.

    The LLM helper is single-turn, so history is folded into the prompt text —
    the same shape the Plan stage uses (history[-8:] as ``role: content`` lines)."""
    lines: list[str] = []
    for turn in ctx.request.history[-8:]:
        role = str(turn.get("role") or "user")
        content = str(turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append(f"user: {ctx.request.message}")
    return "Conversation so far:\n" + "\n".join(lines)


def _results_summary(ctx: PipelineContext) -> str:
    parts: list[str] = []
    for result in ctx.provider_results:
        for tool_result in result.results:
            status = "ok" if tool_result.ok else f"error:{tool_result.error}"
            data = tool_result.data
            blob = json.dumps(data)[:1500] if data is not None else ""
            parts.append(f"[{result.provider}.{tool_result.tool} {status}] {blob}")
    if ctx.blocked:
        parts.append("Blocked pending confirmation: " + ", ".join(c.tool for c in ctx.blocked))
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
