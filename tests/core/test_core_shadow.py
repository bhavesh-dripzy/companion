"""The web->core shadow bridge (companion_api.core_shadow)."""

import pytest

import companion_api.core_shadow as cs
from core.contracts.attachment import Attachment
from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
from core.contracts.turn import TurnResponse
from core.llm.config import ResolvedLlm

pytestmark = pytest.mark.unit


def test_shadow_adapts_request_and_envelope(monkeypatch):
    monkeypatch.setattr(
        cs, "resolve_llm_from_env", lambda *_a: (ResolvedLlm("sarvam", "k", "m"), None)
    )

    class _FakeRunner:
        async def run(self, request, llm):
            assert request.message == "order pizza"
            assert request.user_id == "u1"
            assert request.reply_language == "hinglish"
            return TurnResponse(
                assistant_message="ok",
                task_type="food_delivery",
                routed_provider="zomato",
                attachments=[Attachment(kind="restaurant_card")],
            )

    monkeypatch.setattr(cs, "_get_runner", lambda: _FakeRunner())
    env = cs.run_core_turn_sync(
        {"message": "order pizza", "chat_id": "c1"}, "u1", reply_language="hinglish"
    )
    assert env["pipeline"] == "core"
    assert env["assistant_message"] == "ok"
    assert env["task_type"] == "food_delivery"
    assert env["chat_id"] == "c1"
    assert env["attachments"][0]["kind"] == "restaurant_card"
    assert env["selected_tool_providers"] == []
    assert env["tool_execution"] == {"ok": True}


def test_shadow_envelope_surfaces_providers_and_tool_error(monkeypatch):
    monkeypatch.setattr(
        cs, "resolve_llm_from_env", lambda *_a: (ResolvedLlm("sarvam", "k", "m"), None)
    )

    class _FakeRunner:
        async def run(self, request, llm):
            return TurnResponse(
                assistant_message="I need your location.",
                routed_provider="maps",
                selected_tool_providers=["maps"],
                provider_results=[
                    ProviderResult(
                        provider="maps",
                        ok=False,
                        results=[
                            ToolResult(
                                tool="get_directions",
                                provider="maps",
                                ok=False,
                                data={"ok": False, "error_code": "needs_location"},
                                error=ErrorCode.UPSTREAM_ERROR,
                                error_detail="I need your current location.",
                            )
                        ],
                    )
                ],
            )

    monkeypatch.setattr(cs, "_get_runner", lambda: _FakeRunner())
    env = cs.run_core_turn_sync({"message": "directions home"}, "u1")
    assert env["selected_tool_providers"] == ["maps"]
    assert env["tool_execution"]["ok"] is False
    assert env["tool_execution"]["error_code"] == "needs_location"
    assert env["tool_execution"]["error"] == "I need your current location."


def test_shadow_reports_missing_llm(monkeypatch):
    monkeypatch.setattr(cs, "resolve_llm_from_env", lambda *_a: (None, "no key"))
    env = cs.run_core_turn_sync({"message": "hi"}, "u1")
    assert env["status"] == "error" and env["error"] == "no key"
