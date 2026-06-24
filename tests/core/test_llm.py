"""Unit tests for the core LLM layer (JSON parsing, env resolution)."""

import pytest

from core.llm import config as llm_config
from core.llm.json_mode import parse_json_object

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw",
    [
        '{"intent": "search", "ok": true}',
        '```json\n{"intent": "search", "ok": true}\n```',
        '```\n{"intent": "search", "ok": true}\n```',
        'Here is the plan: {"intent": "search", "ok": true} thanks!',
        '﻿  {"intent": "search", "ok": true}  ',
    ],
)
def test_parse_json_object_handles_real_world_shapes(raw):
    data = parse_json_object(raw)
    assert data["intent"] == "search"
    assert data["ok"] is True


def test_parse_json_object_ignores_braces_inside_strings():
    data = parse_json_object('{"text": "a } b { c", "n": 1}')
    assert data["text"] == "a } b { c"
    assert data["n"] == 1


def test_parse_json_object_rejects_non_object():
    with pytest.raises(ValueError):
        parse_json_object("[1, 2, 3]")


def test_parse_json_object_rejects_garbage():
    with pytest.raises(ValueError):
        parse_json_object("no json here at all")


def _patch_env(monkeypatch, mapping):
    monkeypatch.setattr(
        llm_config, "secret_or_env", lambda name, default="": mapping.get(name, default)
    )
    # _gemini_api_key reads files as a fallback; force it to env-only.
    monkeypatch.setattr(llm_config, "_first_line_secret", lambda _p: "")
    monkeypatch.delenv("GEMINI_API_KEY_FILE", raising=False)


def test_resolve_llm_force_sarvam(monkeypatch):
    _patch_env(monkeypatch, {"SARVAM_API_KEY": "sk"})
    llm, reason = llm_config.resolve_llm_from_env(force="sarvam")
    assert reason is None
    assert llm.provider == "sarvam"
    assert llm.base_url == llm_config.SARVAM_BASE_URL


def test_resolve_llm_force_missing_key_returns_reason(monkeypatch):
    _patch_env(monkeypatch, {})
    llm, reason = llm_config.resolve_llm_from_env(force="openai")
    assert llm is None
    assert "openai" in reason


def test_resolve_llm_autoselect_prefers_sarvam(monkeypatch):
    _patch_env(
        monkeypatch, {"SARVAM_API_KEY": "sk", "GEMINI_API_KEY": "gk", "OPENAI_API_KEY": "ok"}
    )
    llm, reason = llm_config.resolve_llm_from_env()
    assert reason is None
    assert llm.provider == "sarvam"


def test_resolve_llm_autoselect_gemini_over_openai(monkeypatch):
    _patch_env(monkeypatch, {"GEMINI_API_KEY": "gk", "OPENAI_API_KEY": "ok"})
    llm, reason = llm_config.resolve_llm_from_env()
    assert reason is None
    assert llm.provider == "gemini"


def test_resolve_llm_no_keys(monkeypatch):
    _patch_env(monkeypatch, {})
    llm, reason = llm_config.resolve_llm_from_env()
    assert llm is None
    assert "No LLM key" in reason


def test_openai_client_closed_within_call(monkeypatch):
    """Regression: the AsyncOpenAI client must close inside its own coroutine/loop.

    The web bridge runs the pipeline via asyncio.run() per request; a leaked
    client is GC-finalized on a later invocation's loop -> 'Event loop is closed'."""
    import asyncio
    from types import SimpleNamespace

    import openai

    from core.llm.client import _openai_compatible_call
    from core.llm.config import ResolvedLlm

    closed = {"value": False}
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"message": "hi"}'))]
    )

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            async def _create(**_kw):
                return resp

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            closed["value"] = True

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    llm = ResolvedLlm(provider="sarvam", api_key="k", model="m", base_url="https://x")
    out = asyncio.run(_openai_compatible_call(llm=llm, system_prompt="s", user_message="u"))
    assert out == '{"message": "hi"}'
    assert closed["value"] is True
