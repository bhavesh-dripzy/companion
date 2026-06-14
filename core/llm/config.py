"""LLM provider resolution from environment / secrets.

Sarvam (India-hosted, OpenAI-compatible) is the primary provider; Gemini and
OpenAI remain as fallbacks. A `force`/`LLM_PROVIDER` override always wins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:  # secrets manager helper in prod; plain env locally / in tests
    from companion_api.secrets import secret_or_env
except Exception:  # pragma: no cover - import fallback

    def secret_or_env(name: str, default: str = "") -> str:
        return (os.getenv(name) or default).strip()


Provider = Literal["gemini", "openai", "sarvam"]

# Sarvam AI — India-hosted, OpenAI-compatible. Sarvam-M (24B) was deprecated on
# the managed API (mid-2026); sarvam-30b / sarvam-105b are the current models.
SARVAM_MODEL_DEFAULT = "sarvam-30b"
SARVAM_BASE_URL = "https://api.sarvam.ai/v1"
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash-lite"
OPENAI_MODEL_DEFAULT = "gpt-4o-mini"


def _named(name: str, legacy: str, default: str = "") -> str:
    """Canonical env name first, FOOD_ROUTER-era legacy name as fallback.

    The legacy names predate the core/ rewrite and still live in deployed
    secrets (companion/app) and service env — honored here, at the single
    resolution seam, so nothing else in the codebase needs to know them."""
    return secret_or_env(name) or secret_or_env(legacy, default)


@dataclass(frozen=True)
class ResolvedLlm:
    provider: Provider
    api_key: str
    model: str
    base_url: str | None = None  # for OpenAI-compatible providers (Sarvam)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _first_line_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def _gemini_api_key() -> str:
    env_key = (secret_or_env("GEMINI_API_KEY") or secret_or_env("GOOGLE_API_KEY")).strip()
    if env_key:
        return env_key
    fp = (os.getenv("GEMINI_API_KEY_FILE") or "").strip()
    if fp:
        value = _first_line_secret(Path(fp))
        if value:
            return value
    return _first_line_secret(_repo_root() / ".gemini_local")


def _gemini(api_key: str) -> ResolvedLlm:
    return ResolvedLlm(
        "gemini", api_key, _named("GEMINI_MODEL", "GEMINI_FOOD_ROUTER_MODEL", GEMINI_MODEL_DEFAULT)
    )


def _openai(api_key: str) -> ResolvedLlm:
    return ResolvedLlm(
        "openai", api_key, _named("OPENAI_MODEL", "OPENAI_FOOD_ROUTER_MODEL", OPENAI_MODEL_DEFAULT)
    )


def _sarvam(api_key: str) -> ResolvedLlm:
    return ResolvedLlm(
        "sarvam",
        api_key,
        secret_or_env("SARVAM_MODEL", SARVAM_MODEL_DEFAULT),
        base_url=SARVAM_BASE_URL,
    )


def resolve_llm_from_env(force: str = "") -> tuple[ResolvedLlm | None, str | None]:
    """Return (ResolvedLlm, None) when a provider is usable, else (None, reason).

    With no `force` argument, the provider override is read from LLM_PROVIDER
    (legacy FOOD_ROUTER_PROVIDER) here — call sites don't carry env names."""
    sarvam_key = secret_or_env("SARVAM_API_KEY")
    gemini_key = _gemini_api_key()
    openai_key = secret_or_env("OPENAI_API_KEY")
    forced = (force or _named("LLM_PROVIDER", "FOOD_ROUTER_PROVIDER")).strip().lower()

    forced_builders = {
        "sarvam": (sarvam_key, _sarvam),
        "gemini": (gemini_key, _gemini),
        "openai": (openai_key, _openai),
    }
    if forced in forced_builders:
        key, build = forced_builders[forced]
        if not key:
            return None, f"LLM_PROVIDER={forced} but its API key is unset."
        return build(key), None

    # Auto-select: prefer Sarvam (India-hosted), then Gemini, then OpenAI.
    if sarvam_key:
        return _sarvam(sarvam_key), None
    if gemini_key:
        return _gemini(gemini_key), None
    if openai_key:
        return _openai(openai_key), None
    return None, (
        "No LLM key: set SARVAM_API_KEY (preferred), GEMINI_API_KEY / "
        "GOOGLE_API_KEY, or OPENAI_API_KEY."
    )
