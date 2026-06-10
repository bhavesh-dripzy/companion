"""Unit tests for the self-repair middleware (classification, budgets, repair loop)."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from core.contracts.tool_call import ToolCall
from core.contracts.tool_result import ErrorCode
from core.llm.config import ResolvedLlm
from core.middleware.repair import (
    RepairBudget,
    classify_envelope,
    error_text,
    repair_arguments,
)
from core.providers.base import ExecCtx
from core.providers.mcp import mcp_base
from core.providers.mcp.connection import StdioSpec

pytestmark = pytest.mark.unit

_LLM = ResolvedLlm(provider="deepseek", api_key="x", model="m")


class _Provider(mcp_base.McpProvider):
    id = "zomato"
    label = "Zomato"

    def stdio_spec(self, ctx):
        return StdioSpec(command="node", args=[], cwd=".")


class _Raw:
    def __init__(self, data, is_error=False):
        self.isError = is_error
        self.structuredContent = data
        self.content = ()


# ── classification ─────────────────────────────────────────────────────────


def test_classify_validation_text_as_invalid_args():
    env = {
        "is_error": True,
        "data": {
            "raw_text": "1 validation error for call[x]\nnear_and_fast\n  Unexpected keyword argument"
        },
    }
    assert classify_envelope(env) is ErrorCode.INVALID_ARGS
    assert "Unexpected keyword" in error_text(env)


def test_classify_required_property_as_invalid_args():
    env = {
        "is_error": True,
        "data": {"raw_text": "Input validation error: 'filters' is a required property"},
    }
    assert classify_envelope(env) is ErrorCode.INVALID_ARGS


def test_classify_other_errors_as_upstream():
    env = {"is_error": True, "data": {"error": "restaurant is closed right now"}}
    assert classify_envelope(env) is ErrorCode.UPSTREAM_ERROR


def test_repair_budget_caps_per_turn():
    budget = RepairBudget()
    assert budget.take() and budget.take()
    assert not budget.take(), "two repairs per turn, hard cap"


# ── repair_arguments guards ────────────────────────────────────────────────


def _repair(complete_json, schema=None, failed=None):
    return asyncio.run(
        repair_arguments(
            tool="search_movies",
            schema=schema,
            failed_args=failed or {"movie_name": "The Odyssey"},
            server_error="Unexpected keyword argument",
            intent="find showtimes",
            llm=_LLM,
            complete_json=complete_json,
        )
    )


def test_repair_returns_corrected_args():
    async def fake(**kwargs):
        return {
            "arguments": {
                "filters": [{"filter_name": "movie_name", "filter_values": ["The Odyssey"]}]
            }
        }

    out = _repair(fake)
    assert out == {"filters": [{"filter_name": "movie_name", "filter_values": ["The Odyssey"]}]}


def test_repair_rejects_identical_args_and_give_up():
    async def same(**kwargs):
        return {"arguments": {"movie_name": "The Odyssey"}}

    async def gave_up(**kwargs):
        return {"give_up": True, "reason": "cannot derive id"}

    assert _repair(same) is None
    assert _repair(gave_up) is None


def test_repair_discards_schema_invalid_output():
    async def fake(**kwargs):
        return {"arguments": {"bogus": 1}}

    schema = {
        "type": "object",
        "properties": {"filters": {"type": "array"}},
        "additionalProperties": False,
    }
    assert _repair(fake, schema=schema) is None


def test_repair_swallows_llm_failure():
    async def boom(**kwargs):
        raise RuntimeError("LLM down")

    assert _repair(boom) is None


# ── execute_one integration: classify + repair + timeout retry ─────────────


def _wire(monkeypatch, call_log, responses):
    @asynccontextmanager
    async def fake_session(spec, init_timeout=20):
        yield "SESSION"

    async def fake_call_tool(session, name, arguments, timeout=30):
        call_log.append((name, dict(arguments)))
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(mcp_base, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_base, "call_tool", fake_call_tool)


def test_run_repairs_invalid_args_and_succeeds(monkeypatch):
    bad = _Raw({"raw_text": "validation error: Unexpected keyword argument"}, is_error=True)
    good = _Raw({"results": [{"res_id": "1"}]})
    log: list = []
    _wire(monkeypatch, log, [bad, good])

    async def fake_repair_llm(**kwargs):
        return {"arguments": {"keyword": "pizza", "filter": {"near_and_fast": True}}}

    ctx = ExecCtx(llm=_LLM, complete_json=fake_repair_llm, tool_schemas={})
    result = asyncio.run(
        _Provider().run(
            [ToolCall(tool="search", arguments={"keyword": "pizza", "near_and_fast": True})], ctx
        )
    )
    assert result.ok, result.results[0].error_detail
    assert log[1][1] == {"keyword": "pizza", "filter": {"near_and_fast": True}}
    assert ctx.repair_budget.remaining == 1


def test_run_surfaces_typed_error_when_repair_unavailable(monkeypatch):
    bad = _Raw({"raw_text": "validation error: Unexpected keyword argument"}, is_error=True)
    log: list = []
    _wire(monkeypatch, log, [bad])
    result = asyncio.run(
        _Provider().run([ToolCall(tool="search", arguments={"q": "x"})], ExecCtx())
    )
    tr = result.results[0]
    assert tr.error is ErrorCode.INVALID_ARGS and "Unexpected keyword" in tr.error_detail
    assert len(log) == 1, "no repair without an LLM seam"


def test_run_upstream_error_is_not_repaired(monkeypatch):
    bad = _Raw({"error": "kitchen closed"}, is_error=True)
    log: list = []
    _wire(monkeypatch, log, [bad])

    async def must_not_run(**kwargs):
        raise AssertionError("repair must not fire for upstream errors")

    ctx = ExecCtx(llm=_LLM, complete_json=must_not_run)
    result = asyncio.run(_Provider().run([ToolCall(tool="search", arguments={})], ctx))
    assert result.results[0].error is ErrorCode.UPSTREAM_ERROR


def test_timeout_retries_only_idempotent_tools(monkeypatch):
    log: list = []
    _wire(monkeypatch, log, [TimeoutError(), _Raw({"ok": True})])
    ctx = ExecCtx(idempotent_tools=frozenset({"search"}))
    result = asyncio.run(_Provider().run([ToolCall(tool="search", arguments={})], ctx))
    assert result.ok and len(log) == 2, "read-only timeout retried once"

    log2: list = []
    _wire(monkeypatch, log2, [TimeoutError()])
    result2 = asyncio.run(_Provider().run([ToolCall(tool="place_order", arguments={})], ExecCtx()))
    tr = result2.results[0]
    assert tr.error is ErrorCode.TOOL_TIMEOUT and len(log2) == 1, "mutations never re-sent"
