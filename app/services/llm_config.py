from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, NamedTuple

try:
    from companion_api.secrets import secret_or_env
except Exception:
    def secret_or_env(name: str, default: str = "") -> str:
        return (os.getenv(name) or default).strip()


class ResolvedLlm(NamedTuple):
    provider: Literal["gemini", "openai"]
    api_key: str
    model: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _first_line_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def _gemini_api_key() -> str:
    """
    Key resolution order (never commit real keys — use gitignored `.gemini_local` on disk).
    1. GEMINI_API_KEY
    2. GOOGLE_API_KEY
    3. GEMINI_API_KEY_FILE (path to text file containing the key)
    4. <repo-root>/.gemini_local first line (gitignored)
    """
    env_key = (
        secret_or_env("GEMINI_API_KEY") or secret_or_env("GOOGLE_API_KEY")
    ).strip()
    if env_key:
        return env_key
    fp = (os.getenv("GEMINI_API_KEY_FILE") or "").strip()
    if fp:
        v = _first_line_secret(Path(fp))
        if v:
            return v
    return _first_line_secret(_repo_root() / ".gemini_local")


GEMINI_MODEL_DEFAULT = "gemini-2.5-flash-lite"


def resolve_llm_from_env(force: str = "") -> tuple[ResolvedLlm | None, str | None]:
    """
    Return (ResolvedLlm, None) when a provider is usable, otherwise (None, reason).
    """
    gemini_key = _gemini_api_key()
    openai_key = secret_or_env("OPENAI_API_KEY")
    force_l = (force or "").strip().lower()

    if force_l == "openai":
        if not openai_key:
            return None, "FOOD_ROUTER_PROVIDER=openai but OPENAI_API_KEY is unset."
        return ResolvedLlm(
            "openai",
            openai_key,
            secret_or_env("OPENAI_FOOD_ROUTER_MODEL", "gpt-4o-mini"),
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
            secret_or_env("GEMINI_FOOD_ROUTER_MODEL", GEMINI_MODEL_DEFAULT),
        ), None

    if gemini_key:
        return ResolvedLlm(
            "gemini",
            gemini_key,
            secret_or_env("GEMINI_FOOD_ROUTER_MODEL", GEMINI_MODEL_DEFAULT),
        ), None
    if openai_key:
        return ResolvedLlm(
            "openai",
            openai_key,
            secret_or_env("OPENAI_FOOD_ROUTER_MODEL", "gpt-4o-mini"),
        ), None
    return None, (
        "No LLM key: set GEMINI_API_KEY or GOOGLE_API_KEY (preferred), "
        "or OPENAI_API_KEY."
    )
