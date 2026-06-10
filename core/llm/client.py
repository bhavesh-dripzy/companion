"""Async single-turn JSON completion across providers.

Async-first replacement for the sync `complete_json_object` (ADR-0003): one event
loop per request, no `asyncio.run`. Provider calls are separate coroutines so
tests can monkeypatch them without network.
"""

from __future__ import annotations

from typing import Any

from core.llm.config import ResolvedLlm
from core.llm.json_mode import parse_json_object

_TIMEOUT_SECONDS = 40.0


async def _openai_compatible_call(
    *, llm: ResolvedLlm, system_prompt: str, user_message: str
) -> str:
    from openai import AsyncOpenAI

    # max_retries: SDK-native backoff for connection errors / 408 / 429 / 5xx.
    # The ONLY transport-retry layer for LLM calls — no outer wrapper (it would
    # multiply attempts).
    kwargs: dict[str, Any] = {
        "api_key": llm.api_key,
        "timeout": _TIMEOUT_SECONDS,
        "max_retries": 2,
    }
    if llm.provider == "deepseek" and llm.base_url:
        kwargs["base_url"] = llm.base_url
    async with AsyncOpenAI(**kwargs) as client:
        completion = await client.chat.completions.create(
            model=llm.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message.strip()},
            ],
            response_format={"type": "json_object"},
        )
    return completion.choices[0].message.content or "{}"


async def _gemini_call(*, llm: ResolvedLlm, system_prompt: str, user_message: str) -> str:
    try:
        from google import genai as google_genai
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError("Gemini provider requires google-genai") from exc

    client = google_genai.Client(api_key=llm.api_key)
    response = await client.aio.models.generate_content(
        model=llm.model,
        contents=f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_message.strip()}\n",
    )
    return response.text or "{}"


async def _provider_call(*, llm: ResolvedLlm, system_prompt: str, user_message: str) -> str:
    if llm.provider in ("openai", "deepseek"):
        return await _openai_compatible_call(
            llm=llm, system_prompt=system_prompt, user_message=user_message
        )
    return await _gemini_call(llm=llm, system_prompt=system_prompt, user_message=user_message)


async def complete_json_object(
    *, user_message: str, system_prompt: str, llm: ResolvedLlm
) -> dict[str, Any]:
    """Single-turn JSON object from the model (orchestrator + planners + narrator).

    Transport retries live in the SDK (max_retries). Unparseable output gets ONE
    re-ask with the parse error appended (Instructor pattern), then surfaces."""
    content = await _provider_call(llm=llm, system_prompt=system_prompt, user_message=user_message)
    try:
        return parse_json_object(content)
    except ValueError as exc:
        retry_message = (
            f"{user_message}\n\nYour previous reply was not valid JSON ({exc}). "
            "Reply again with ONLY the JSON object."
        )
        content = await _provider_call(
            llm=llm, system_prompt=system_prompt, user_message=retry_message
        )
        return parse_json_object(content)
