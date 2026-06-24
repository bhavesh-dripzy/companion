"""Unit tests for Devanagari → Hinglish romanization (no network)."""

import asyncio

import pytest

from companion_api import transliterate

pytestmark = pytest.mark.unit


def test_contains_devanagari():
    assert transliterate.contains_devanagari("मैं ghar ja raha hoon")
    assert not transliterate.contains_devanagari("main ghar ja raha hoon")
    assert not transliterate.contains_devanagari("")
    assert not transliterate.contains_devanagari(None)


def test_pure_roman_text_skips_llm(monkeypatch):
    def boom(**kwargs):
        raise AssertionError("LLM must not be called for pure-Roman text")

    monkeypatch.setattr(transliterate, "complete_json_object", boom)
    assert asyncio.run(transliterate.romanize_hinglish("order pizza")) == "order pizza"


def test_devanagari_text_romanized(monkeypatch):
    async def fake_complete(**kwargs):
        return {"romanized": "main ghar ja raha hoon"}

    monkeypatch.setattr(transliterate, "complete_json_object", fake_complete)
    monkeypatch.setattr(transliterate, "resolve_llm_from_env", lambda *_: (object(), None))
    out = asyncio.run(transliterate.romanize_hinglish("मैं घर जा रहा हूं"))
    assert out == "main ghar ja raha hoon"


def test_llm_failure_returns_original(monkeypatch):
    async def fake_complete(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(transliterate, "complete_json_object", fake_complete)
    monkeypatch.setattr(transliterate, "resolve_llm_from_env", lambda *_: (object(), None))
    original = "मैं घर जा रहा हूं"
    assert asyncio.run(transliterate.romanize_hinglish(original)) == original


def test_empty_llm_payload_returns_original(monkeypatch):
    async def fake_complete(**kwargs):
        return {}

    monkeypatch.setattr(transliterate, "complete_json_object", fake_complete)
    monkeypatch.setattr(transliterate, "resolve_llm_from_env", lambda *_: (object(), None))
    original = "मैं घर जा रहा हूं"
    assert asyncio.run(transliterate.romanize_hinglish(original)) == original
