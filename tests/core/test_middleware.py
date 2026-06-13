"""Unit tests for the tool-calling middleware."""

import asyncio

import pytest

from core.contracts.tool import AuthMode, ConfirmPolicy, Tool
from core.contracts.tool_call import ToolCall, ToolPlan
from core.contracts.tool_result import ErrorCode, ToolResult
from core.middleware.confirmation import partition
from core.middleware.dispatcher import Dispatcher
from core.middleware.errors import AuthRequiredError, result_from_exception
from core.middleware.planner_prompt import build_planner_prompt, render_tools_block
from core.middleware.validator import normalize_plan
from core.providers.base import ExecCtx
from core.providers.runner import run_calls
from core.registry import ProviderRegistry, ToolRegistry

pytestmark = pytest.mark.unit


# ── planner prompt ────────────────────────────────────────────────────────


def test_render_tools_block_sorted_with_confirm_notes():
    tools = [
        Tool(name="search_restaurants", provider="z", description="Find places"),
        Tool(name="checkout_cart", provider="z", confirm=ConfirmPolicy.SINGLE),
        Tool(name="place_order", provider="k", confirm=ConfirmPolicy.TWO_STEP),
    ]
    block = render_tools_block(tools)
    lines = block.splitlines()
    assert lines[0].startswith("- checkout_cart")  # sorted by name
    assert "needs explicit user confirmation" in block
    assert "mandatory two-step" in block
    assert "Find places" in block


def test_build_planner_prompt_fills_slot_or_appends():
    tools = [Tool(name="t", provider="p")]
    assert "- t" in build_planner_prompt("Pre.\n{{TOOLS}}\nPost.", tools)
    appended = build_planner_prompt("Just a preamble.", tools)
    assert "TOOLS (use ONLY these exact tool names):" in appended


def test_location_continuity_attaches_only_for_geo_planners():
    tools = [Tool(name="t", provider="p")]
    # A preamble that opts into geo (documents the constraint) inherits the rule.
    geo = build_planner_prompt(
        'set constraints.requested_location_text to the named place.\n{{TOOLS}}', tools
    )
    assert "LOCATION CONTINUITY" in geo
    # A non-geo planner (no requested_location_text) never gets the noise.
    non_geo = build_planner_prompt("Plan kite trades.\n{{TOOLS}}", tools)
    assert "LOCATION CONTINUITY" not in non_geo
    # A planner that already documents its own continuity isn't double-stamped.
    own = build_planner_prompt(
        "requested_location_text rules; LOCATION CONTINUITY handled here.\n{{TOOLS}}", tools
    )
    assert own.count("LOCATION CONTINUITY") == 1


def test_arg_hint_from_params():
    tool = Tool(
        name="x",
        provider="p",
        params={"type": "object", "properties": {"q": {}, "n": {}}, "required": ["q"]},
    )
    block = render_tools_block([tool])
    assert "args: q*, n" in block


# ── validator ─────────────────────────────────────────────────────────────


def _registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        Tool(name="get_addresses", provider="swiggy_food", aliases=("get_user_addresses",))
    )
    tools.register(Tool(name="search_restaurants", provider="swiggy_food"))
    return tools


def test_normalize_plan_drops_unknown_and_resolves_alias():
    raw = {
        "intent": "order",
        "summary": "find food",
        "tool_plan": [
            {"tool": "get_user_addresses", "arguments": {}},  # alias -> get_addresses
            {"tool": "search_restaurants", "arguments": {"query": "pizza"}},
            {"tool": "hallucinated_tool", "arguments": {}},  # dropped
            {"not_a_dict": True},
        ],
    }
    plan, dropped = normalize_plan(raw, provider="swiggy_food", tools=_registry())
    assert [c.tool for c in plan.calls] == ["get_addresses", "search_restaurants"]
    assert plan.calls[1].arguments == {"query": "pizza"}
    assert dropped == 1
    assert plan.intent == "order"


def test_normalize_plan_empty_when_no_tool_plan():
    plan, dropped = normalize_plan({"intent": "chat"}, provider="swiggy_food", tools=_registry())
    assert plan.is_empty
    assert dropped == 0


# ── confirmation ──────────────────────────────────────────────────────────


def test_partition_blocks_mutating_until_confirmed():
    tools = ToolRegistry()
    tools.register(Tool(name="search", provider="z"))
    tools.register(Tool(name="checkout", provider="z", confirm=ConfirmPolicy.SINGLE))
    plan = ToolPlan(calls=[ToolCall(tool="search"), ToolCall(tool="checkout")])

    allowed, blocked = partition(plan, provider="z", tools=tools, confirmed=False)
    assert [c.tool for c in allowed] == ["search"]
    assert [c.tool for c in blocked] == ["checkout"]

    allowed2, blocked2 = partition(plan, provider="z", tools=tools, confirmed=True)
    assert len(allowed2) == 2 and not blocked2


# ── errors ────────────────────────────────────────────────────────────────


def test_result_from_exception_maps_code():
    res = result_from_exception("login", "kite", AuthRequiredError("needs login"))
    assert res.failed and res.error is ErrorCode.AUTH_REQUIRED
    generic = result_from_exception("t", "p", RuntimeError("boom"))
    assert generic.error is ErrorCode.UNKNOWN and "boom" in generic.error_detail


# ── dispatcher (fan-out, placeholder flow, failure isolation) ─────────────


class _FakeProvider:
    """A provider whose per-call behaviour is injected; `run` uses `run_calls`."""

    def __init__(self, pid, behavior):
        self.id = pid
        self.label = pid
        self.auth_mode = AuthMode.NONE
        self.session_cls = None
        self._behavior = behavior

    async def discover_tools(self):
        return []

    async def run(self, calls, ctx):
        async def execute_one(call):
            return self._behavior(call, ctx)

        return await run_calls(execute_one, calls=calls, provider_id=self.id, ctx=ctx)

    def build_attachments(self, results):
        return []

    def merge(self, slice_, results):
        return slice_


class _BrokenProvider(_FakeProvider):
    async def run(self, calls, ctx):  # whole-provider failure before any call runs
        raise AuthRequiredError("connect failed")


def _dispatch_registry(providers, tools_map):
    tools = ToolRegistry()
    for name, pid in tools_map.items():
        tools.register(Tool(name=name, provider=pid))
    reg = ProviderRegistry(tools=tools)
    for prov in providers:
        reg.register(prov)
    return reg


def test_dispatch_fans_out_isolates_failures_and_flows_placeholders():
    def alpha_exec(call, ctx):
        if call.tool == "a1":  # learns a placeholder for the next alpha call
            return ToolResult(tool=call.tool, provider="alpha", learned_placeholders={"__X__": "1"})
        if call.tool == "a2":  # sees the placeholder a1 learned (intra-provider flow)
            return ToolResult(
                tool=call.tool, provider="alpha", data={"saw": ctx.placeholders.get("__X__")}
            )
        raise AuthRequiredError("no token")  # "boom"

    def beta_exec(call, ctx):
        return ToolResult(tool=call.tool, provider="beta")

    reg = _dispatch_registry(
        [_FakeProvider("alpha", alpha_exec), _FakeProvider("beta", beta_exec)],
        {"a1": "alpha", "a2": "alpha", "boom": "alpha", "b1": "beta"},
    )
    results = asyncio.run(
        Dispatcher(reg).dispatch(
            [ToolCall(tool="a1"), ToolCall(tool="a2"), ToolCall(tool="boom"), ToolCall(tool="b1")],
            ctx_for=lambda pid: ExecCtx(),
        )
    )
    by_provider = {r.provider: r for r in results}
    assert by_provider["alpha"].results[1].data == {"saw": "1"}  # placeholder flowed a1 -> a2
    assert by_provider["alpha"].results[2].error is ErrorCode.AUTH_REQUIRED  # boom isolated
    assert not by_provider["alpha"].ok
    assert by_provider["beta"].ok  # beta unaffected by alpha's failure


def test_dispatch_isolates_whole_provider_failure_and_unsupported_tools():
    reg = _dispatch_registry(
        [
            _BrokenProvider("alpha", None),
            _FakeProvider("beta", lambda c, ctx: ToolResult(tool=c.tool, provider="beta")),
        ],
        {"a1": "alpha", "b1": "beta"},
    )
    results = asyncio.run(
        Dispatcher(reg).dispatch(
            [ToolCall(tool="a1"), ToolCall(tool="b1"), ToolCall(tool="orphan")],
            ctx_for=lambda pid: ExecCtx(),
        )
    )
    by_provider = {r.provider: r for r in results}
    assert (
        by_provider["alpha"].results[0].error is ErrorCode.AUTH_REQUIRED
    )  # provider-level failure
    assert by_provider["beta"].ok
    assert by_provider["_unsupported"].results[0].error_detail == "no provider owns this tool"
