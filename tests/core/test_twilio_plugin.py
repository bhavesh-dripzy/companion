"""The Twilio plugin — the #27 proof: a stateless provider added via plugin + 1 line."""

import asyncio

import pytest

from core.contracts.tool import AuthMode, ConfirmPolicy
from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode
from core.providers.base import ExecCtx
from core.providers.plugins.twilio import TwilioProvider
from core.providers.plugins.twilio import client as twilio_client
from core.registry.bootstrap import build_registry

pytestmark = pytest.mark.unit


def test_declared_tools_shape():
    tools = {t.name: t for t in TwilioProvider().declared_tools()}
    assert set(tools) == {"make_phone_call", "get_call_status"}
    call = tools["make_phone_call"]
    assert call.confirm is ConfirmPolicy.SINGLE and call.mutating
    assert call.auth_mode is AuthMode.NONE and call.source == "static"
    assert call.params["required"] == ["to_number", "message"]
    assert tools["get_call_status"].confirm is ConfirmPolicy.NEVER


def test_registry_wires_twilio_with_real_params():
    registry = build_registry()
    assert isinstance(registry.get("twilio"), TwilioProvider)
    # The plugin's real schema replaced the empty seed params.
    assert registry.tools.get("make_phone_call", provider="twilio").params["properties"]


def test_run_make_phone_call_success(monkeypatch):
    monkeypatch.setattr(
        twilio_client,
        "make_call",
        lambda **kw: {"ok": True, "call_sid": "CA1", "status": "queued"},
    )
    result = asyncio.run(
        TwilioProvider().run(
            [
                ToolCall(
                    tool="make_phone_call", arguments={"to_number": "9876543210", "message": "Hi"}
                )
            ],
            ExecCtx(),
        )
    )
    assert result.ok
    assert result.results[0].data["call_sid"] == "CA1"


def test_run_missing_args_is_invalid_args():
    result = asyncio.run(
        TwilioProvider().run(
            [ToolCall(tool="make_phone_call", arguments={"message": "Hi"})], ExecCtx()
        )
    )
    assert result.results[0].error is ErrorCode.INVALID_ARGS


def test_run_upstream_failure_is_typed(monkeypatch):
    monkeypatch.setattr(twilio_client, "make_call", lambda **kw: {"ok": False, "error": "no creds"})
    result = asyncio.run(
        TwilioProvider().run(
            [
                ToolCall(
                    tool="make_phone_call",
                    arguments={"to_number": "+919876543210", "message": "Hi"},
                )
            ],
            ExecCtx(),
        )
    )
    assert not result.ok
    assert result.results[0].error is ErrorCode.UPSTREAM_ERROR
