"""Kite raw-transport parsing, result shims, and the session re-attach seam."""

import json

import pytest

import core.providers.plugins.kite.transport as kt
from core.providers.base import ExecCtx
from core.providers.mcp.result_parse import tool_result_payload
from core.providers.plugins.kite.provider import KiteProvider

pytestmark = pytest.mark.unit


def test_parse_rpc_text_sse_and_json():
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    assert kt.parse_rpc_text(sse, "text/event-stream")["result"] == {"ok": True}
    assert kt.parse_rpc_text('{"jsonrpc":"2.0","id":1,"result":{}}', "application/json") == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }
    assert kt.parse_rpc_text("not json", "application/json") is None


def test_result_shim_parses_like_sdk_result():
    rpc = {
        "jsonrpc": "2.0",
        "id": 5,
        "result": {
            "content": [{"type": "text", "text": json.dumps({"data": {"net": []}})}],
            "isError": False,
        },
    }
    envelope = tool_result_payload(kt.result_shim(rpc))
    assert envelope["is_error"] is False
    assert envelope["data"] == {"data": {"net": []}}


def test_result_shim_rpc_error_becomes_error_envelope():
    envelope = tool_result_payload(
        kt.result_shim({"jsonrpc": "2.0", "id": 5, "error": {"message": "session not found"}})
    )
    assert envelope["is_error"] is True


def test_probe_profile_classification(monkeypatch):
    class R:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.text = json.dumps(payload)
            self.headers = {"Content-Type": "application/json"}

        @property
        def ok(self):
            return self.status_code < 400

    profile = {
        "result": {
            "content": [{"type": "text", "text": json.dumps({"data": {"user_name": "Aashish"}})}],
            "isError": False,
        }
    }
    monkeypatch.setattr(kt, "_post_sync", lambda body, sid=None: R(200, profile))
    out = kt.probe_profile("kitemcp-abc")
    assert out == {"authed": True, "session_alive": True, "profile": "Aashish"}

    unauthed = {"result": {"content": [{"type": "text", "text": "please login"}], "isError": True}}
    monkeypatch.setattr(kt, "_post_sync", lambda body, sid=None: R(200, unauthed))
    out = kt.probe_profile("kitemcp-abc")
    assert out["authed"] is False and out["session_alive"] is True

    monkeypatch.setattr(
        kt, "_post_sync", lambda body, sid=None: R(200, {"error": {"message": "no session"}})
    )
    assert kt.probe_profile("kitemcp-abc")["session_alive"] is False


def test_kite_provider_reattaches_vaulted_session(monkeypatch):
    provider = KiteProvider()
    monkeypatch.setattr(
        "core.providers.plugins.kite.transport.vaulted_sid", lambda user: "kitemcp-xyz"
    )
    cm = provider.open_session(ExecCtx(user_id="alice"))
    assert cm.__class__.__name__ == "_AsyncGeneratorContextManager"

    monkeypatch.setattr("core.providers.plugins.kite.transport.vaulted_sid", lambda user: None)
    cm2 = provider.open_session(ExecCtx(user_id="alice"))
    assert cm2 is not None and cm2 is not cm


def test_vault_helpers_roundtrip(monkeypatch):
    store = {}
    monkeypatch.setattr(kt, "mcp_token_secret", lambda user: store.get(user, {}))
    monkeypatch.setattr(
        kt,
        "merge_mcp_token_secret",
        lambda user, **kw: store.setdefault(user, {}).update(kw),
    )
    assert kt.vaulted_sid("alice") is None
    kt.save_sid("alice", "kitemcp-1")
    assert kt.vaulted_sid("alice") == "kitemcp-1"
    kt.clear_sid("alice")
    assert kt.vaulted_sid("alice") is None
