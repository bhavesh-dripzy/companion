"""Unit tests for the core LLM layer (JSON parsing, env resolution, fn-call schema)."""

import pytest

from core.contracts.tool import Tool
from core.llm import config as llm_config
from core.llm.function_calling import tools_for_llm
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


def test_resolve_llm_force_deepseek(monkeypatch):
    _patch_env(monkeypatch, {"DEEPSEEK_API_KEY": "dk"})
    llm, reason = llm_config.resolve_llm_from_env(force="deepseek")
    assert reason is None
    assert llm.provider == "deepseek"
    assert llm.base_url == llm_config.DEEPSEEK_BASE_URL


def test_resolve_llm_force_missing_key_returns_reason(monkeypatch):
    _patch_env(monkeypatch, {})
    llm, reason = llm_config.resolve_llm_from_env(force="openai")
    assert llm is None
    assert "openai" in reason


def test_resolve_llm_autoselect_prefers_gemini(monkeypatch):
    _patch_env(monkeypatch, {"GEMINI_API_KEY": "gk", "OPENAI_API_KEY": "ok"})
    llm, reason = llm_config.resolve_llm_from_env()
    assert reason is None
    assert llm.provider == "gemini"


def test_resolve_llm_no_keys(monkeypatch):
    _patch_env(monkeypatch, {})
    llm, reason = llm_config.resolve_llm_from_env()
    assert llm is None
    assert "No LLM key" in reason


def test_function_calling_formats():
    tool = Tool(
        name="make_phone_call", provider="twilio", description="Call", params={"type": "object"}
    )
    openai = tools_for_llm([tool], "openai")[0]
    gemini = tools_for_llm([tool], "gemini")[0]
    anthropic = tools_for_llm([tool], "anthropic")[0]
    assert openai["type"] == "function"
    assert openai["function"]["name"] == "make_phone_call"
    assert gemini["name"] == "make_phone_call"
    assert anthropic["input_schema"] == {"type": "object"}
