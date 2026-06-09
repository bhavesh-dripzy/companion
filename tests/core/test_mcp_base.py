"""Unit tests for the shared MCP base: placeholders, address, result-parse, run()."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode
from core.providers.base import ExecCtx
from core.providers.mcp import mcp_base
from core.providers.mcp.address import extract_address_rows, first_address_id
from core.providers.mcp.connection import StdioSpec
from core.providers.mcp.placeholders import PlaceholderResolver
from core.providers.mcp.result_parse import tool_result_payload

pytestmark = pytest.mark.unit


# ── placeholders ──────────────────────────────────────────────────────────


def test_placeholder_substitute_and_unresolved():
    r = PlaceholderResolver()
    obj = {"cart_id": "__CART_ID__", "items": [{"id": "__RESTAURANT_ID__"}]}
    assert r.unresolved(obj) == ["__CART_ID__", "__RESTAURANT_ID__"]
    out = r.substitute(obj, {"__CART_ID__": "C1", "__RESTAURANT_ID__": "R1"})
    assert out == {"cart_id": "C1", "items": [{"id": "R1"}]}
    assert r.unresolved(out) == []


def test_placeholder_learn_dual_key_and_first_wins():
    r = PlaceholderResolver()
    values: dict[str, str] = {}
    # camelCase addressId must be harvested (Swiggy dual-key); first value wins.
    r.learn(values, {"data": {"addresses": [{"addressId": "A1"}, {"addressId": "A2"}]}})
    assert values["__ADDRESS_ID__"] == "A1"


def test_placeholder_clear_stale_on_issuing_tool():
    r = PlaceholderResolver()
    values = {"__CART_ID__": "old", "__ADDRESS_ID__": "keep"}
    r.clear_stale(values, "create_cart")  # create_cart mints a fresh cart id
    assert "__CART_ID__" not in values
    assert values["__ADDRESS_ID__"] == "keep"


# ── address extraction (dual-key) ─────────────────────────────────────────


def test_extract_address_rows_handles_all_id_spellings_and_nesting():
    payload = {
        "data": {
            "result": {
                "addresses": [
                    {"address_id": "1", "addressTag": "Home", "address_line": "12 MG Rd"},
                    {"addressId": "2", "location_name": "Office"},
                    {"id": "3"},
                    {"no_id": True},  # dropped
                ]
            }
        }
    }
    rows = extract_address_rows(payload)
    assert [r["address_id"] for r in rows] == ["1", "2", "3"]
    assert rows[0]["label"] == "Home — 12 MG Rd"
    assert first_address_id(payload) == "1"


# ── result parsing ────────────────────────────────────────────────────────


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Result:
    def __init__(self, *, isError=False, structuredContent=None, content=()):
        self.isError = isError
        self.structuredContent = structuredContent
        self.content = content


def test_tool_result_payload_prefers_structured_content():
    out = tool_result_payload(_Result(structuredContent={"cart_id": "C1"}))
    assert out == {"is_error": False, "data": {"cart_id": "C1"}}


def test_tool_result_payload_parses_text_and_image_blocks():
    res = _Result(
        content=(
            _Block(type="text", text='{"ok": true}'),
            _Block(type="image", data="QQ==", mimeType="image/png"),
        )
    )
    out = tool_result_payload(res)
    assert out["images"][0]["data_b64"] == "QQ=="
    assert out["data"]["blocks"][0] == {"ok": True}


# ── McpProvider.run (one session, substitution, learning) ─────────────────


def _fake_result(data):
    return _Result(structuredContent=data)


def test_mcp_run_uses_one_session_and_flows_placeholders(monkeypatch):
    opened: list = []

    @asynccontextmanager
    async def fake_session(spec, init_timeout=20):
        opened.append(spec)
        yield "SESSION"

    seen: list = []

    async def fake_call_tool(session, name, arguments, timeout=30):
        seen.append((name, dict(arguments)))
        return _fake_result({"cart_id": "CART9"} if name == "create_cart" else {"ok": True})

    monkeypatch.setattr(mcp_base, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_base, "call_tool", fake_call_tool)

    class _Provider(mcp_base.McpProvider):
        id = "zomato"
        label = "Zomato"

        def stdio_spec(self, ctx):
            return StdioSpec(command="node", args=[], cwd=".")

    result = asyncio.run(
        _Provider().run(
            [
                ToolCall(tool="create_cart", arguments={}),
                ToolCall(tool="checkout_cart", arguments={"cart_id": "__CART_ID__"}),
            ],
            ExecCtx(),
        )
    )
    assert len(opened) == 1  # ONE session for the whole group
    assert seen[1] == ("checkout_cart", {"cart_id": "CART9"})  # __CART_ID__ flowed forward
    assert result.ok


def test_mcp_run_blocks_unresolved_placeholder(monkeypatch):
    @asynccontextmanager
    async def fake_session(spec, init_timeout=20):
        yield "SESSION"

    async def fake_call_tool(session, name, arguments, timeout=30):  # should not be reached
        raise AssertionError("call_tool ran despite unresolved placeholder")

    monkeypatch.setattr(mcp_base, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_base, "call_tool", fake_call_tool)

    class _Provider(mcp_base.McpProvider):
        id = "zomato"
        label = "Zomato"

        def stdio_spec(self, ctx):
            return StdioSpec(command="node", args=[], cwd=".")

    result = asyncio.run(
        _Provider().run(
            [ToolCall(tool="checkout_cart", arguments={"cart_id": "__CART_ID__"})], ExecCtx()
        )
    )
    assert result.results[0].error is ErrorCode.UNRESOLVED_PLACEHOLDER
