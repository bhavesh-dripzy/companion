"""Cutover wiring + shadow-parity: clean Zomato provider ≈ LegacyMcpAdapter.

Verifies (1) the CLEAN_MCP flag swaps the registered provider class, and (2) on
the SAME real captured flow the clean `ZomatoProvider` and the `LegacyMcpAdapter`
agree on the user-visible essentials — the restaurant the turn surfaced appears in
the cards and is carried forward in session_restaurants from both paths.
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.legacy.adapter import LegacyMcpAdapter
from core.providers.plugins.zomato.provider import ZomatoProvider
from core.registry.bootstrap import build_registry

pytestmark = pytest.mark.component

_FIXTURE = json.loads((Path(__file__).parent / "fixtures/zomato/search_pizza.json").read_text())


def _provider_result() -> ProviderResult:
    # Both result.results (clean path) and diagnostics (adapter path) from the
    # one real capture, so both providers read their native input.
    results = [
        ToolResult(
            tool=step["tool"],
            provider="zomato",
            ok=not (step["result"] or {}).get("is_error"),
            arguments=step.get("arguments") or {},
            data=step["result"],
        )
        for step in _FIXTURE["steps"]
    ]
    return ProviderResult(
        provider="zomato", results=results, ok=_FIXTURE["ok"], diagnostics=_FIXTURE
    )


def test_clean_mcp_flag_swaps_the_registered_provider(monkeypatch):
    monkeypatch.setenv("CLEAN_MCP", "zomato")
    assert isinstance(build_registry().get("zomato"), ZomatoProvider)

    monkeypatch.setenv("CLEAN_MCP", "")  # explicit empty -> all adapters
    assert isinstance(build_registry().get("zomato"), LegacyMcpAdapter)


def test_default_cutover_runs_zomato_clean_rest_adapter(monkeypatch):
    monkeypatch.delenv("CLEAN_MCP", raising=False)
    reg = build_registry()
    assert isinstance(reg.get("zomato"), ZomatoProvider), "zomato is cut over by default"
    # kite is the last provider still on the proven adapter (order-ticket round-trip).
    assert isinstance(reg.get("kite"), LegacyMcpAdapter)


def test_shadow_parity_clean_vs_adapter_on_real_capture(monkeypatch):
    result = _provider_result()
    clean = ZomatoProvider()
    monkeypatch.setenv("CLEAN_MCP", "")  # adapter path
    adapter = build_registry().get("zomato")

    clean_cards = json.dumps([c.to_wire() for c in clean.build_attachments(result)])
    adapter_cards = json.dumps([c.to_wire() for c in adapter.build_attachments(result)])
    # The real restaurant the live turn cited must appear from BOTH paths.
    assert "20324557" in clean_cards and "20324557" in adapter_cards

    clean_sr = clean.merge({}, result).get("session_restaurants") or []
    adapter_sr = adapter.merge({}, result).get("session_restaurants") or []
    clean_ids = {str(r.get("res_id")) for r in clean_sr}
    adapter_ids = {str(r.get("res_id")) for r in adapter_sr}
    assert "20324557" in clean_ids and "20324557" in adapter_ids
    # Parity on the carried-forward restaurant set (order/extra-fields may differ).
    assert clean_ids == adapter_ids
