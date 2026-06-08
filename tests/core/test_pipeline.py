"""End-to-end pipeline tests with a fake provider + mocked LLM (no network/AWS).

Proves the staged main pipeline (Personalize -> ... -> Persist) wires together
before any real provider exists: routing, planning, the confirmation gate, fan-out,
attachment collection, narration, and the KG-seam publish.
"""

import asyncio

import pytest

from core.app_factory import build_runner
from core.boundaries.null_impls import NoopPublisher
from core.contracts.attachment import Attachment
from core.contracts.profile import Profile, SavedAddress
from core.contracts.tool import AuthMode
from core.contracts.tool_result import ToolResult
from core.contracts.turn import TurnRequest
from core.llm.config import ResolvedLlm
from core.providers.runner import run_calls
from core.registry import ProviderRegistry, ToolRegistry
from core.registry.seed import builtin_tools

pytestmark = pytest.mark.unit

_LLM = ResolvedLlm(provider="deepseek", api_key="x", model="m")


class _FakeZomato:
    id = "zomato"
    label = "Zomato"
    auth_mode = AuthMode.OAUTH
    session_cls = None

    async def discover_tools(self):
        return []

    async def run(self, calls, ctx):
        async def execute_one(call):
            return ToolResult(tool=call.tool, provider="zomato", data={"ran": call.tool})

        return await run_calls(execute_one, calls=calls, provider_id="zomato", ctx=ctx)

    def build_attachments(self, results):
        return [Attachment(kind="restaurant_card", tool=r.tool) for r in results if r.ok]

    def merge(self, slice_, results):
        return {**slice_, "last_tools": [r.tool for r in results]}


def _fake_complete_json(reply="Here are some options."):
    async def complete(*, user_message, system_prompt, llm):
        if "machine-ready tool plan" in system_prompt:  # planner
            tool = "checkout_cart" if "checkout" in user_message.lower() else "search_restaurants"
            return {
                "intent": "order",
                "summary": "x",
                "tool_plan": [{"tool": tool, "arguments": {}}],
            }
        if "You are Companion" in system_prompt:  # narrator
            if "Blocked pending confirmation" in user_message:
                return {"message": "Please confirm to proceed with checkout."}
            return {"message": reply}
        return {
            "task_type": "food_delivery",
            "food_providers": ["zomato"],
            "reasoning": "food",
        }  # orchestrator

    return complete


def _runner(profile=None, publisher=None, complete=None):
    tools = ToolRegistry()
    tools.register_all(builtin_tools())
    registry = ProviderRegistry(tools=tools)
    registry.register(_FakeZomato())

    class _Profile:
        async def get(self, user_id):
            return profile

    return build_runner(
        registry=registry,
        profile_service=_Profile(),
        publisher=publisher or NoopPublisher(),
        complete_json=complete or _fake_complete_json(),
    )


def test_full_turn_routes_plans_executes_collects_narrates_publishes():
    publisher = NoopPublisher()
    runner = _runner(publisher=publisher)
    response = asyncio.run(runner.run(TurnRequest(message="order pizza", user_id="u1"), _LLM))
    assert response.task_type == "food_delivery"
    assert response.routed_provider == "zomato"
    assert response.assistant_message == "Here are some options."
    assert any(a.kind == "restaurant_card" for a in response.attachments)
    assert response.session.providers["zomato"]["last_tools"] == ["search_restaurants"]
    assert publisher.published and publisher.published[0].user_id == "u1"
    # observable trace records every stage
    events = {e["event"] for e in response.observable_trace}
    assert {"personalize", "route", "plan", "execute", "collect", "narrate", "persist"} <= events


def test_address_auto_resolves_office_from_profile():
    profile = Profile(
        user_id="u1",
        addresses={"office": SavedAddress(provider_address_id="OFF1", address_line="WeWork")},
    )
    runner = _runner(profile=profile)
    response = asyncio.run(
        runner.run(TurnRequest(message="order biryani to office", user_id="u1"), _LLM)
    )
    assert response.session.selected_address_id == "OFF1"


def test_confirmation_gate_blocks_then_allows_checkout():
    # Unconfirmed: planner emits checkout_cart but the gate holds it back.
    runner = _runner()
    blocked = asyncio.run(runner.run(TurnRequest(message="checkout my cart", user_id="u1"), _LLM))
    assert not blocked.session.providers.get("zomato")  # nothing ran
    assert "confirm" in blocked.assistant_message.lower()

    # Confirmed: the same checkout runs.
    runner2 = _runner()
    done = asyncio.run(
        runner2.run(TurnRequest(message="confirm checkout my cart", user_id="u1"), _LLM)
    )
    assert done.session.providers["zomato"]["last_tools"] == ["checkout_cart"]
