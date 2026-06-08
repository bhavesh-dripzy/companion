"""The web->core shadow bridge (companion_api.core_shadow)."""

import pytest

import companion_api.core_shadow as cs
from core.contracts.attachment import Attachment
from core.contracts.turn import TurnResponse
from core.llm.config import ResolvedLlm

pytestmark = pytest.mark.unit


def test_shadow_adapts_request_and_envelope(monkeypatch):
    monkeypatch.setattr(cs, "resolve_llm_from_env", lambda _x: (ResolvedLlm("deepseek", "k", "m"), None))

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


def test_shadow_reports_missing_llm(monkeypatch):
    monkeypatch.setattr(cs, "resolve_llm_from_env", lambda _x: (None, "no key"))
    env = cs.run_core_turn_sync({"message": "hi"}, "u1")
    assert env["status"] == "error" and env["error"] == "no key"
