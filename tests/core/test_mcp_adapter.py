"""The LegacyMcpAdapter — runs MCP providers via their legacy executors (migration)."""

import asyncio

import pytest

from core.contracts.tool import AuthMode
from core.contracts.tool_call import ToolCall
from core.providers.base import ExecCtx
from core.providers.legacy import adapter as adapter_mod
from core.providers.legacy.adapter import LegacyMcpAdapter, LegacyMcpConfig
from core.registry.bootstrap import build_registry

pytestmark = pytest.mark.unit


def test_registry_wires_all_mcp_providers_as_adapters():
    registry = build_registry()
    for pid in ("zomato", "swiggy_food", "swiggy_instamart", "zepto", "district", "kite"):
        assert isinstance(registry.get(pid), LegacyMcpAdapter)
        # tools still come from the seed catalog during migration
        assert registry.tools.for_provider(pid)


def test_adapter_passes_session_and_merges_diagnostics():
    captured: dict = {}

    def fake_run(tool_plan, **kwargs):
        captured.update(kwargs)
        captured["tool_plan"] = tool_plan
        return {
            "ok": True,
            "steps": [
                {
                    "tool": "create_cart",
                    "arguments": {},
                    "result": {"is_error": False, "data": {"cart_id": "C1"}},
                }
            ],
            "diagnostics": {"session_menu": [{"variant_id": "v1"}]},
        }

    cfg = LegacyMcpConfig(
        id="zomato",
        label="Z",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="unused:x",
        run_fn=fake_run,
        url_env="ZOMATO_MCP_URL",
        default_url="https://example",
        session_map={"session_menu": "menu", "session_cart": "cart"},
    )
    adapter = LegacyMcpAdapter(cfg)
    ctx = ExecCtx(
        user_id="u1",
        address_id="A1",
        session={"session_menu": [{"variant_id": "old"}]},  # flat legacy bag
        placeholders={"__X__": "1"},
    )
    result = asyncio.run(adapter.run([ToolCall(tool="create_cart", arguments={})], ctx))

    assert result.ok
    assert result.results[0].data["data"]["cart_id"] == "C1"
    assert captured["address_id"] == "A1"
    assert captured["session_placeholders"] == {"__X__": "1"}
    assert captured["session_menu"] == [{"variant_id": "old"}]  # bag fed into the executor
    # merge writes the executor's new session_menu (from diagnostics) back to the bag
    merged = adapter.merge(ctx.session, result)
    assert merged["session_menu"] == [{"variant_id": "v1"}]


def test_adapter_build_attachments_calls_legacy_collectors(monkeypatch):
    def fake_collector(raw_tool_results):
        steps = raw_tool_results["zomato"]["steps"]
        return [{"kind": "restaurant_card", "count": len(steps)}]

    monkeypatch.setattr(adapter_mod, "_resolve", lambda path: fake_collector)
    cfg = LegacyMcpConfig(
        id="zomato",
        label="Z",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="unused:x",
        run_fn=lambda tp, **k: {"ok": True, "steps": [{"tool": "x", "result": {}}]},
        collector_paths=("pipeline:_collect",),
    )
    adapter = LegacyMcpAdapter(cfg)
    result = asyncio.run(adapter.run([ToolCall(tool="x")], ExecCtx()))
    attachments = adapter.build_attachments(result)
    assert attachments[0].kind == "restaurant_card"
