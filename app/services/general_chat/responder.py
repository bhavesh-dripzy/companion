"""General-chat domain agent: user message → assistant reply (LLM + prompt only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.services.llm_config import ResolvedLlm

_PROMPTS = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=1)
def _load_prompt() -> str:
    return (_PROMPTS / "chat.system.md").read_text(encoding="utf-8").strip()


def _conversation_messages(
    *,
    user_message: str,
    conversation_history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    current = (user_message or "").strip()
    out: list[dict[str, str]] = []
    if conversation_history:
        for item in conversation_history[-8:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            out.append({"role": str(role), "content": content[:700]})
    if not out or out[-1].get("role") != "user" or out[-1].get("content") != current:
        if current:
            out.append({"role": "user", "content": current})
    return out


def _openai_compatible_chat(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    base_url: str | None = None,
) -> str:
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    completion = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[{"role": "system", "content": system_prompt}, *messages],
    )
    return (completion.choices[0].message.content or "").strip()


def reply_general_chat(
    *,
    user_message: str,
    llm: ResolvedLlm,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """General-chat agent turn: direct conversational reply (no MCP)."""
    system_prompt = _load_prompt()
    messages = _conversation_messages(
        user_message=user_message,
        conversation_history=conversation_history,
    )
    if not messages:
        return "I'm here — what would you like to know?"

    try:
        if llm.provider in ("openai", "deepseek"):
            text = _openai_compatible_chat(
                api_key=llm.api_key,
                model=llm.model,
                system_prompt=system_prompt,
                messages=messages,
                base_url=llm.base_url if llm.provider == "deepseek" else None,
            )
            if text:
                return text

        from app.services.food_llm_router import _gemini_generate_text

        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in messages
        )
        text = _gemini_generate_text(
            api_key=llm.api_key,
            model=llm.model,
            system_prompt=system_prompt,
            user_message=transcript,
            label="general_chat",
        ).strip()
        if text:
            return text
    except Exception:
        pass

    return "I'm sorry, I couldn't generate a reply right now. Please try again."
