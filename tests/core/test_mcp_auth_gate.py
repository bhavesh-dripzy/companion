"""Pre-flight OAuth auth gate: token-usability classification + fast-fail in run().

The gate stops an OAuth provider with a missing or dead-and-unrefreshable token
from spawning mcp-remote (which would fall into an interactive browser handshake
that hangs to the init timeout on Lambda). It raises AuthRequiredError instead,
which the dispatcher turns into a clean AUTH_REQUIRED result.
"""

import asyncio
import base64
import json
import os
import time
from contextlib import asynccontextmanager

import pytest

from core.contracts.tool import AuthMode
from core.contracts.tool_call import ToolCall
from core.middleware.errors import AuthRequiredError
from core.providers.base import ExecCtx
from core.providers.mcp import mcp_base, token_store
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.remote import effective_config_dir
from core.providers.mcp.token_store import has_usable_tokens, server_url_hash

pytestmark = pytest.mark.unit

_URL = "https://mcp.example.com/x"


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"hdr.{payload}.sig"


def _write_tokens(tokens: dict, *, age_s: float = 0.0) -> None:
    cfg = effective_config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    path = cfg / f"{server_url_hash(_URL)}_tokens.json"
    path.write_text(json.dumps(tokens), encoding="utf-8")
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ── has_usable_tokens classification ──────────────────────────────────────


def test_usable_false_when_no_token_file(cfgdir):
    assert has_usable_tokens(mcp_url=_URL) is False


def test_usable_true_with_refresh_token_even_if_access_expired(cfgdir):
    _write_tokens({"access_token": _jwt(int(time.time()) - 9999), "refresh_token": "r"})
    assert has_usable_tokens(mcp_url=_URL) is True


def test_usable_false_when_jwt_expired_and_no_refresh(cfgdir):
    _write_tokens({"access_token": _jwt(int(time.time()) - 9999)})
    assert has_usable_tokens(mcp_url=_URL) is False


def test_usable_true_when_jwt_valid_and_no_refresh(cfgdir):
    _write_tokens({"access_token": _jwt(int(time.time()) + 9999)})
    assert has_usable_tokens(mcp_url=_URL) is True


def test_usable_true_when_opaque_unknown_expiry(cfgdir):
    # Opaque token, no expires_in, no refresh — unknown, treated as usable (no false gating).
    _write_tokens({"access_token": "opaque-token"})
    assert has_usable_tokens(mcp_url=_URL) is True


def test_usable_false_when_opaque_expires_in_elapsed(cfgdir):
    _write_tokens({"access_token": "opaque", "expires_in": 3600}, age_s=7200)
    assert has_usable_tokens(mcp_url=_URL) is False


# ── the gate inside McpProvider.run ───────────────────────────────────────


def _patch_session(monkeypatch) -> list:
    opened: list = []

    @asynccontextmanager
    async def fake_session(spec, init_timeout=20):
        opened.append(spec)
        yield "SESSION"

    async def fake_call_tool(session, name, arguments, timeout=30):
        from tests.core.test_mcp_base import _fake_result

        return _fake_result({"ok": True})

    monkeypatch.setattr(mcp_base, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_base, "call_tool", fake_call_tool)
    return opened


class _OAuthProvider(mcp_base.McpProvider):
    id = "swiggy_instamart"
    label = "Swiggy Instamart MCP"
    auth_mode = AuthMode.OAUTH

    def mcp_url(self, ctx):
        return _URL

    def stdio_spec(self, ctx):
        return StdioSpec(command="node", args=[], cwd=".")


def _stub_ensure(monkeypatch):
    import core.providers.mcp.token_vault as token_vault

    monkeypatch.setattr(token_vault, "ensure_mcp_tokens_on_disk", lambda *a, **k: {})


def test_gate_raises_auth_required_and_skips_session(monkeypatch):
    opened = _patch_session(monkeypatch)
    _stub_ensure(monkeypatch)
    monkeypatch.setattr(token_store, "has_usable_tokens", lambda *a, **k: False)

    with pytest.raises(AuthRequiredError) as exc:
        asyncio.run(_OAuthProvider().run([ToolCall(tool="search_products", arguments={})], ExecCtx()))

    assert "Swiggy Instamart" in str(exc.value.detail)  # label, " MCP" stripped
    assert opened == []  # mcp-remote never spawned


def test_gate_passes_when_tokens_usable(monkeypatch):
    opened = _patch_session(monkeypatch)
    _stub_ensure(monkeypatch)
    monkeypatch.setattr(token_store, "has_usable_tokens", lambda *a, **k: True)

    result = asyncio.run(
        _OAuthProvider().run([ToolCall(tool="search_products", arguments={})], ExecCtx())
    )
    assert result.ok
    assert len(opened) == 1  # session opened normally


def test_gate_skipped_when_no_mcp_url(monkeypatch):
    # Base default mcp_url -> None disables the gate (the existing test providers).
    opened = _patch_session(monkeypatch)

    class _NoUrl(_OAuthProvider):
        def mcp_url(self, ctx):
            return None

    result = asyncio.run(_NoUrl().run([ToolCall(tool="t", arguments={})], ExecCtx()))
    assert result.ok
    assert len(opened) == 1


def test_gate_skipped_for_non_oauth(monkeypatch):
    opened = _patch_session(monkeypatch)

    class _NoneAuth(_OAuthProvider):
        auth_mode = AuthMode.NONE

    result = asyncio.run(_NoneAuth().run([ToolCall(tool="t", arguments={})], ExecCtx()))
    assert result.ok
    assert len(opened) == 1


# ── partial success: a valid provider still works when another needs re-auth ──


def test_dispatcher_serves_valid_provider_when_another_needs_reauth():
    from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult
    from core.middleware.dispatcher import Dispatcher
    from core.registry.provider_registry import ProviderRegistry

    class _Healthy:
        id = "zomato"
        label = "Zomato"
        auth_mode = AuthMode.OAUTH
        session_cls = None

        async def run(self, calls, ctx):
            return ProviderResult(
                provider=self.id,
                results=[
                    ToolResult(tool=c.tool, provider=self.id, ok=True, data={"ok": True})
                    for c in calls
                ],
                ok=True,
            )

    class _Expired:
        id = "swiggy_food"
        label = "Swiggy Food MCP"
        auth_mode = AuthMode.OAUTH
        session_cls = None

        async def run(self, calls, ctx):
            raise AuthRequiredError(detail="Swiggy Food isn't connected — reconnect it to continue.")

    reg = ProviderRegistry()
    reg.register(_Healthy())
    reg.register(_Expired())
    calls = [
        ToolCall(tool="search_restaurants", arguments={}, provider="zomato"),
        ToolCall(tool="search_restaurants", arguments={}, provider="swiggy_food"),
    ]
    results = asyncio.run(Dispatcher(reg).dispatch(calls, lambda pid: ExecCtx()))
    by = {r.provider: r for r in results}
    assert by["zomato"].ok and by["zomato"].results[0].ok  # valid provider still served
    assert not by["swiggy_food"].ok
    assert by["swiggy_food"].results[0].error is ErrorCode.AUTH_REQUIRED
    assert "reconnect" in (by["swiggy_food"].results[0].error_detail or "").lower()


def test_tool_execution_flags_reauth_provider_on_partial_success():
    from companion_api.core_shadow import _tool_execution
    from core.contracts.tool_result import ErrorCode, ProviderResult, ToolResult

    healthy = ProviderResult(
        provider="zomato",
        results=[ToolResult(tool="search_restaurants", provider="zomato", ok=True, data={"ok": True})],
        ok=True,
    )
    expired = ProviderResult(
        provider="swiggy_food",
        results=[
            ToolResult(
                tool="search_restaurants",
                provider="swiggy_food",
                ok=False,
                error=ErrorCode.AUTH_REQUIRED,
                error_detail="reconnect Swiggy Food",
            )
        ],
        ok=False,
        error=ErrorCode.AUTH_REQUIRED,
        error_detail="reconnect Swiggy Food",
    )
    out = _tool_execution([healthy, expired])
    assert out["reauth_needed_by"] == ["swiggy_food"]
    assert out["error_code"] == "auth_required"
