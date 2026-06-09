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
    "Given the user's message and any tool results, write a short, helpful reply.\n"
    "Reply ONLY in this language/style: {language}.\n"
    "If tools were blocked pending confirmation, ask the user to confirm.\n"
    'Respond with ONLY a JSON object: {{"message": "<reply>"}}.'
)


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
        user = f"User: {ctx.request.message}\n\nTool results:\n{_results_summary(ctx)}"
        try:
            raw = await ctx.deps.complete_json(user_message=user, system_prompt=system, llm=ctx.llm)
            ctx.assistant_message = str(raw.get("message") or "").strip()
        except Exception:
            ctx.assistant_message = ""
        if not ctx.assistant_message:
            ctx.assistant_message = _fallback_message(ctx)
        ctx.tracer.event("narrate", chars=len(ctx.assistant_message))
