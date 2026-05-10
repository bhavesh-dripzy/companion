from __future__ import annotations

import os
from typing import Literal, NamedTuple


class ResolvedLlm(NamedTuple):
    provider: Literal["gemini", "openai"]
    api_key: str
    model: str


def resolve_llm_from_env(force: str = "") -> tuple[ResolvedLlm | None, str | None]:
    """
    Return (ResolvedLlm, None) when a provider is usable, otherwise (None, reason).
    """
    gemini_key = (
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    ).strip()
    openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    force_l = (force or "").strip().lower()

    if force_l == "openai":
        if not openai_key:
            return None, "FOOD_ROUTER_PROVIDER=openai but OPENAI_API_KEY is unset."
        return ResolvedLlm(
            "openai",
            openai_key,
            os.getenv("OPENAI_FOOD_ROUTER_MODEL", "gpt-4o-mini"),
        ), None
    if force_l == "gemini":
        if not gemini_key:
            return None, (
                "FOOD_ROUTER_PROVIDER=gemini but GEMINI_API_KEY / GOOGLE_API_KEY "
                "is unset."
            )
        return ResolvedLlm(
            "gemini",
            gemini_key,
            os.getenv("GEMINI_FOOD_ROUTER_MODEL", "gemini-2.5-flash-lite"),
        ), None

    if gemini_key:
        return ResolvedLlm(
            "gemini",
            gemini_key,
            os.getenv("GEMINI_FOOD_ROUTER_MODEL", "gemini-2.5-flash-lite"),
        ), None
    if openai_key:
        return ResolvedLlm(
            "openai",
            openai_key,
            os.getenv("OPENAI_FOOD_ROUTER_MODEL", "gpt-4o-mini"),
        ), None
    return None, (
        "No LLM key: set GEMINI_API_KEY or GOOGLE_API_KEY (preferred), "
        "or OPENAI_API_KEY."
    )
