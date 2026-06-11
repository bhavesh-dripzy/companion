"""Full tool-surface coverage — every operation each MCP server exposes must be
registered, plannable, and produce narrator-usable output.

Live tools/list for all six servers was diffed against the seed registry on
2026-06-10 (exact parity, 95 tools; snapshot committed to
docs/architecture/mcp-live-tool-catalog.json, re-checkable via
scripts/mcp_tool_drift.py). These tests pin the static halves: seed↔snapshot
parity, the generic lean digest that makes order/tracking/coupon/quote
envelopes readable (they previously reached the narrator as a 1500-char
truncated blob), and the completed __ORDER_ID__ issuer rules.
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ToolResult
from core.providers.mcp.digest import lean_digest
from core.providers.mcp.placeholders import DEFAULT_ISSUERS, PlaceholderResolver
from core.registry.seed import _SPECS

pytestmark = pytest.mark.unit

_SNAPSHOT = Path(__file__).resolve().parents[2] / "docs/architecture/mcp-live-tool-catalog.json"


def test_seed_matches_live_catalog_snapshot():
    snapshot = json.loads(_SNAPSHOT.read_text())
    seeded = {pid: set(names.split()) for pid, _l, _a, names in _SPECS}
    for pid, info in snapshot.items():
        live = {t["name"] for t in info["tools"]}
        assert live == seeded[pid], (
            f"{pid}: seed registry diverged from the live catalog snapshot — "
            f"run scripts/mcp_tool_drift.py and update seed.py"
        )


def test_every_seeded_mcp_provider_is_in_snapshot():
    snapshot = json.loads(_SNAPSHOT.read_text())
    mcp_providers = {
        pid for pid, _l, _a, _n in _SPECS if pid not in ("twilio", "daily_calorie_tracker")
    }
    assert mcp_providers == set(snapshot), "snapshot must cover every MCP provider"


def test_lean_digest_keeps_facts_drops_noise():
    envelope = {
        "data": {
            "orders": [
                {
                    "order_id": "OD123",
                    "status": "DELIVERED",
                    "total": 472,
                    "date": "2026-06-08",
                    "image_url": "https://cdn.example.com/x.png",
                    "deeplink": "app://order/OD123",
                    "items": [{"name": "Margherita", "quantity": 1, "price": 109}],
                }
            ]
        }
    }
    digest = lean_digest(envelope)
    assert "DELIVERED" in digest and "472" in digest and "Margherita" in digest
    assert "cdn.example.com" not in digest and "deeplink" not in digest


def test_lean_digest_caps_rows_and_reports_overflow():
    envelope = {"data": {"rows": [{"name": f"r{i}", "price": i} for i in range(30)]}}
    digest = lean_digest(envelope)
    assert "r0" in digest and "r11" in digest
    assert "r12" not in digest and "(+18 more)" in digest


def test_lean_digest_compresses_numeric_series():
    """Chart series (e.g. a market sparkline) digest to a range summary, not
    a dozen raw floats in the narrator prompt; short scalar lists still list."""
    envelope = {"data": {"spark": [23257.25 + i for i in range(48)], "amenities": ["AC", "Wifi"]}}
    digest = lean_digest(envelope)
    assert "48 values: 23257.25 → 23304.25" in digest
    assert "23258.25" not in digest
    assert "AC" in digest and "Wifi" in digest


def test_lean_digest_empty_data_is_none():
    assert lean_digest({"data": {}}) is None
    assert lean_digest({}) is None
    assert lean_digest(None) is None


def test_default_summarize_result_uses_lean_digest():
    from core.providers.mcp.mcp_base import McpProvider

    class _P(McpProvider):
        id = "x"

        def stdio_spec(self, ctx):  # pragma: no cover - unused
            raise NotImplementedError

    tr = ToolResult(
        tool="get_order_history",
        provider="x",
        ok=True,
        data={"data": {"orders": [{"order_id": "1", "status": "DELIVERED"}]}},
    )
    digest = _P().summarize_result(tr)
    assert digest and "DELIVERED" in digest


def test_order_minting_tools_clear_stale_order_id():
    resolver = PlaceholderResolver()
    for tool in (
        "checkout",
        "create_order",
        "create_online_payment_order",
        "create_wallet_order",
        "create_upi_reserve_pay_order",
    ):
        assert "__ORDER_ID__" in DEFAULT_ISSUERS[tool]
        values = {"__ORDER_ID__": "stale"}
        resolver.clear_stale(values, tool)
        assert "__ORDER_ID__" not in values, tool


def test_calorie_tracker_provider_is_registered():
    """health_nutrition routed to a ghost provider before: the orchestrator and
    seed knew daily_calorie_tracker but no plugin was registered, so every
    health turn dispatched into the void."""
    from core.registry.bootstrap import build_registry

    provider = build_registry().get("daily_calorie_tracker")
    assert provider is not None
    assert provider.id == "daily_calorie_tracker"


def test_calorie_tracker_stdio_spec_isolates_users():
    from core.providers.base import ExecCtx
    from core.providers.plugins.daily_calorie_tracker import DailyCalorieTrackerProvider

    p = DailyCalorieTrackerProvider()
    spec_a = p.stdio_spec(ExecCtx(user_id="alice"))
    spec_b = p.stdio_spec(ExecCtx(user_id="bob../../x"))
    assert spec_a.command == "node"
    assert spec_a.args[0].endswith("daily-calorie-tracker-mcp/dist/index.js")
    assert spec_a.env["HOME"] != spec_b.env["HOME"]
    user_segment = spec_b.env["HOME"].split("companion-stdio-mcp/")[-1].split("/")[0]
    assert (
        user_segment == "bob.._.._x"
    ), "user id must sanitize to a single path segment (no traversal)"
