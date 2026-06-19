"""Same-class writes collapse onto one provider; reads keep fanning out."""

from types import SimpleNamespace

from core.contracts.tool import Tool
from core.contracts.tool_call import ToolCall, ToolPlan
from core.middleware.disambiguation import collapse_same_class_writes
from core.registry.tool_registry import ToolRegistry


def _registry():
    tools = ToolRegistry()
    for prov in ("swiggy_instamart", "zepto"):
        tools.register_all(
            [
                Tool(name="update_cart", provider=prov, mutating=True),
                Tool(name="search_products", provider=prov, mutating=False),
            ]
        )
    providers = {
        "swiggy_instamart": SimpleNamespace(
            capability_class="grocery", cart_session_key="session_instamart_cart"
        ),
        "zepto": SimpleNamespace(capability_class="grocery", cart_session_key="session_zepto_cart"),
    }
    return SimpleNamespace(tools=tools, get=lambda pid: providers.get(pid))


def _plan(provider: str, *tools: str) -> ToolPlan:
    return ToolPlan(provider=provider, calls=[ToolCall(tool=t) for t in tools])


def _names(plan: ToolPlan) -> list[str]:
    return [c.tool for c in plan.calls]


def test_both_write_keeps_seed_drops_partner():
    plans = {
        "swiggy_instamart": _plan("swiggy_instamart", "update_cart", "search_products"),
        "zepto": _plan("zepto", "update_cart", "search_products"),
    }
    events = collapse_same_class_writes(plans, _registry(), {})
    assert _names(plans["swiggy_instamart"]) == ["update_cart", "search_products"]
    assert _names(plans["zepto"]) == ["search_products"]  # write dropped, read kept
    assert events == [{"capability": "grocery", "kept": "swiggy_instamart", "dropped": ["zepto"]}]


def test_partner_only_write_still_collapses_to_seed():
    # The audit case: Instamart deferred (read only), Zepto planned the cart write.
    plans = {
        "swiggy_instamart": _plan("swiggy_instamart", "search_products"),
        "zepto": _plan("zepto", "update_cart"),
    }
    events = collapse_same_class_writes(plans, _registry(), {})
    assert _names(plans["zepto"]) == []  # Zepto's stray write suppressed
    assert events == [{"capability": "grocery", "kept": "swiggy_instamart", "dropped": ["zepto"]}]


def test_active_cart_wins_over_seed():
    plans = {
        "swiggy_instamart": _plan("swiggy_instamart", "update_cart"),
        "zepto": _plan("zepto", "update_cart"),
    }
    legacy = {"session_zepto_cart": {"items": [{"spin_id": "x"}]}}
    events = collapse_same_class_writes(plans, _registry(), legacy)
    assert _names(plans["zepto"]) == ["update_cart"]
    assert _names(plans["swiggy_instamart"]) == []
    assert events == [{"capability": "grocery", "kept": "zepto", "dropped": ["swiggy_instamart"]}]


def test_pure_reads_are_left_fanned_out():
    plans = {
        "swiggy_instamart": _plan("swiggy_instamart", "search_products"),
        "zepto": _plan("zepto", "search_products"),
    }
    events = collapse_same_class_writes(plans, _registry(), {})
    assert _names(plans["zepto"]) == ["search_products"]
    assert events == []


def test_single_provider_untouched():
    plans = {"swiggy_instamart": _plan("swiggy_instamart", "update_cart")}
    events = collapse_same_class_writes(plans, _registry(), {})
    assert _names(plans["swiggy_instamart"]) == ["update_cart"]
    assert events == []
